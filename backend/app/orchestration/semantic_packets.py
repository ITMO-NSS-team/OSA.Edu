from __future__ import annotations

from ..rules.contracts import fact_items
from ..rules.manifest import load_rule_manifest
from ..document.fact_store import fact_store_prompt_text


RULE_GUIDANCE = {
    rule_id: entry.engine.guidance
    for rule_id, entry in load_rule_manifest().rules.items()
    if entry.engine.guidance
}

# Compatibility/readability snapshot. Facts themselves live in the manifest.
ABSENCE_RULES = {
    rule_id: list(entry.engine.facts)
    for rule_id, entry in load_rule_manifest().rules.items()
    if entry.engine.kind.value == 'semantic_fact'
}

def _message(map_value:dict,fragment:dict,rules:list[dict],fact_store:dict|None=None)->str:
    summary='\n'.join(f"{e.get('type')} | {e.get('label')} | {e.get('startBlockId')}…{e.get('endBlockId')}" for e in map_value.get('elements',[]))
    blocks='\n\n'.join(f"BLOCK {b['id']} | {b.get('location','')}"+(f" | page={b['page']}" if b.get('page') else '')+f" | type={b.get('type','paragraph')}\n{b.get('text','')}" for b in fragment.get('blocks',[]))
    chunks=[]
    for rule in rules:
        required=fact_items(rule['id']); absence=''
        if required:
            absence=f'''\nТИП ПРОВЕРКИ: FACT-FIRST / отсутствие элемента. Просмотри ВСЕ {len(fragment.get('blocks',[]))} блоков фрагмента. Главная задача — извлечь факты, а не выбрать финальный verdict: Python рассчитает его отдельно и проигнорирует твой status для этого правила. Помимо обычных полей верни absenceCheck: {{"complete": boolean, "checkedBlockCount": number, "items": [{{"name": string, "status": "found"|"not_found"|"ambiguous", "reason": "коротко", "evidence": [{{"blockId": string, "quote": string}}], "candidates": [{{"label": "кандидат только из BLOCK", "relation": "почему это возможный аналог/baseline/прототип", "evidence": [{{"blockId": string, "quote": string}}]}}]}}]}}. Обязательные элементы: {', '.join(required)}. Если есть один или несколько правдоподобных candidates, но нельзя выбрать единственный — status=ambiguous, НИКОГДА not_found. not_found означает, что после полного просмотра не найден даже правдоподобный кандидат.'''
        guidance = rule.get('ruleGuidance') or RULE_GUIDANCE.get(rule['id'])
        guidance_text = f"\nУТОЧНЕНИЕ ПРОВЕРКИ: {guidance}" if guidance else ''
        chunks.append(f"RULE {rule['id']}\nКатегория: {rule.get('category','')}\nТребование: {rule.get('requirement','')}\nКорректный пример: {rule.get('correctExample') or '—'}\nПример нарушения: {rule.get('incorrectExample') or '—'}{guidance_text}{absence}")
    semantic_context = str(fragment.get('semanticContext') or '').strip()
    semantic_section = f"\nSEMANTIC_CONTEXT (контекст для понимания; evidence всё равно только из BLOCK):\n{semantic_context}\n" if semantic_context else ''
    fact_keys = []
    for rule in rules:
        fact_keys.extend(rule.get('globalFactKeys') or [])
    global_facts = fact_store_prompt_text(fact_store, fact_keys)
    global_section = f"\nGLOBAL_DOCUMENT_FACTS (grounded facts, построенные Python один раз для всего документа; использовать как контекст, но evidence нарушения всё равно брать из BLOCK):\n{global_facts}\n" if global_facts else ''
    return f'''DOCUMENT_MAP:\n{summary}\n\nCHECK_FRAGMENT:\nid={fragment['id']}\nlabel={fragment['label']}\ncomplete={str(fragment.get('complete',False)).lower()}\ntotalBlocks={len(fragment.get('blocks',[]))}{semantic_section}{global_section}\n{blocks}\n\nRULES:\n{'\n\n'.join(chunks)}\n\nОБЯЗАТЕЛЬНОЕ ОГРАНИЧЕНИЕ: используй только факты и названия, которые присутствуют в BLOCK, SEMANTIC_CONTEXT или GLOBAL_DOCUMENT_FACTS. Внешние знания запрещены. GLOBAL_DOCUMENT_FACTS можно использовать, чтобы не объявлять термин необъяснённым, если его grounded-определение уже найдено в другой части документа. Не предлагай в explanation/fix новые методы, статьи, продукты, авторов или бенчмарки, которых нет во входном тексте. Если документ не даёт основания для конкретного совета, формулируй исправление обобщённо.\n\nВерни JSON: {{"results":[{{"ruleId":"...","status":"pass|violation|uncertain|not_applicable","explanation":"...","fix":"...","evidence":[{{"blockId":"...","quote":"точная непрерывная цитата"}}],"absenceCheck":...}}]}}.'''


def _fact_recovery_message(fragment: dict, rule: dict, fact_store: dict | None = None) -> str:
    required = fact_items(str(rule.get('id'))) or []
    blocks = '\n\n'.join(
        f"BLOCK {b['id']} | {b.get('location','')}"
        + (f" | page={b.get('page')}" if b.get('page') is not None else '')
        + f" | type={b.get('type','paragraph')}\n{b.get('text','')}"
        for b in fragment.get('blocks', [])
    )
    fact_rows = ','.join(
        '{"name":"'+name+'","status":"found|not_found|ambiguous","reason":"коротко","evidence":[{"blockId":"...","quote":"точная цитата"}],"candidates":[]}'
        for name in required
    )
    global_facts = fact_store_prompt_text(fact_store, rule.get('globalFactKeys') or [])
    global_section = f"\nGLOBAL_DOCUMENT_FACTS:\n{global_facts}\n" if global_facts else ''
    return f'''RECOVERY FACT-FIRST. Проверяется только одна сущность и одно правило.{global_section}
Не формируй pass/violation: Python проигнорирует status. Для status=found ОБЯЗАТЕЛЬНО дай хотя бы одну точную evidence-цитату из BLOCK; found без проверяемого evidence будет автоматически понижен до ambiguous. Просмотри ВСЕ {len(fragment.get('blocks', []))} BLOCK.
Внешние знания запрещены; кандидаты и evidence только из BLOCK.

FRAGMENT: {fragment.get('label')}
SEMANTIC_CONTEXT: {fragment.get('semanticContext','')}

RULE {rule.get('id')}: {rule.get('requirement','')}
FACTS: {', '.join(required)}

{blocks}

Верни только JSON:
{{"results":[{{"ruleId":"{rule.get('id')}","status":"uncertain","explanation":"fact extraction","evidence":[],"absenceCheck":{{"complete":true,"checkedBlockCount":{len(fragment.get('blocks', []))},"items":[{fact_rows}]}}}}]}}
Если есть правдоподобный кандидат, но выбор не однозначен, используй ambiguous, не not_found.'''
