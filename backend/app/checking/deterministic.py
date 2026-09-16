from __future__ import annotations
import regex as re
from .common import (grounded_check, evidence, contextual, dedupe_evidence, narrative_blocks, result, is_actual_caption,
    is_code_or_prompt, formula_like_block, is_likely_table_context, looks_like_contents)
from .bibliography import run_bibliography_rule
from ..document.numbered_items import extract_numbered_items, collect_numbered_items, collect_unique_defense_items
from ..scope import main_work_ids

RULE_FALLBACK={'CORE-1-4':'defense-punctuation','CORE-1-5':'defense-symbols','SOFT-023':'defense-punctuation','SOFT-024':'defense-symbols'}

SPECS={
'lexical-replacements':[(r'\bнужно\b','Заменить «нужно» на «необходимо».'),(r'\bзначит\b','Заменить «значит» на «следовательно». '),(r'\bзаключается\s+в\b','Проверить замену на «состоит в».')],
'obvious-claims':[(r'\b(?:очевидно|несомненно|легко\s+видеть|хорошо\s+известно|довольно\s+очевидно)\b','Убрать оценочное слово и привести обоснование или ссылку.')],
'praise-claims':[(r'\b(?:уникальн\p{L}*|высокоэффективн\p{L}*|совершенно\s+бесспорн\p{L}*|значительн\p{L}*\s+вклад|наглядно\s+демонстрир\p{L}*)\b','Заменить оценку измеримым сравнением или нейтральным утверждением.')],
'title-process':[(r'^(?:разработка|исследование|изучение)\b','Переформулировать название через научный результат, а не процесс.')],
'title-vague-efficiency':[(r'\bповышени[ея]\s+(?:эффективности|качества)\b','Указать конкретный измеримый результат.')],
'bibliography-junk':[(r'\bISBN\b',None),(r'\bed\.\s+by\b',None),(r'\b[A-ZА-ЯЁ][\p{L}-]+,\s+[A-ZА-ЯЁ]\.', 'Убрать запятую между фамилией и инициалами и унифицировать формат.'),(r'\s&\s','Убрать символ & и оформить авторов единообразно.')],
'bibliography-pages':[(r'\bp\.\s*\d+\s*[-–—]\s*\d+\b','Для диапазона страниц англоязычной статьи использовать «pp. 12-25».')],
'forbidden-sentence-start':[(r'(?:^|[.!?]\s+)(?:А|Но|Так\s+как|То\s+есть|Т\.?\s*к\.|Т\.?\s*е\.)\s+','Перестроить начало предложения.')],
'forbidden-abbreviations':[(r'\b(?:т\.\s*е\.|т\.\s*к\.|т\.\s*ч\.)','Раскрыть сокращение.'),(r'\bт\.[дп]\.','Добавить пробел: «т. д.», «т. п.».')],
'colon-a-imenno':[(r':\s*а\s+именно\b','Убрать «а именно».')],
'to-est':[(r'\bто\s+есть\b','Перестроить пояснение.')],
'colon-and-to-est':[(r':\s*а\s+именно\b','Убрать избыточную конструкцию.'),(r'\bто\s+есть\b','Перестроить пояснение.')],
'diminutives':[(r'\b(?:лампочка|программка|строчка|стрелочка|кнопочка|табличка)\b','Использовать нейтральную форму.')],
'see-figure':[(r'\bсм\.\s*(?:рис|рисунок|табл|таблицу)\.?\s*\d+','Убрать «см.» и сослаться непосредственно.')],
'first-new':[(r'\b(?:впервые|нов(?:ый|ая|ое|ые)\s+(?:метод|алгоритм|модель|подход))\b','Убрать утверждение либо обосновать пионерский характер.')],
'heading-final-period':[(r'\.$','Убрать точку в конце.')],
'above-written':[(r'\b(?:вышеизложенн\p{L}*|вышеперечисленн\p{L}*)\b','Заменить на нормативную конструкцию.')],
'lexical-cliches':[(r'\bвидится\b','Заменить на точный глагол, например «является».'),(r'\bпредставляет\s+(?:важное|практическое|теоретическое)\s+значение\b','Использовать «имеет ... значение».'),(r'\bвыглядит\s+как\b','Использовать «представляет собой», если это соответствует смыслу.')],
'jargon':[(r'\b(?:мапать|маппинг|буст|спринт|бэклог|стейкхолдер|бейзлайн|даунтайм|дашборд|эпик(?:и|ов)?)\b','Заменить жаргон русским термином или пояснить.')],
'goal-infinitive':[(r'\bцель(?:ю)?\s+(?:работы|исследования)?\s*(?:является|состоит\s+в|–|-|:)\s*(?:разработать|исследовать|создать|реализовать|построить|автоматизировать|повысить|улучшить)\b','Сформулировать цель как результат существительным.')],
'python-capitalization':[(r'\bпитон(?:а|е|ом|ы|ов)?\b','Писать Python.')],
'initials-order':[(r'\b[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]\.\s+[А-ЯЁA-Z][\p{L}-]+','В библиографии ставить инициалы после фамилии.')],
'decimal-comma':[(r'\b\d+\.\d+\b','В русскоязычном тексте использовать запятую, исключая версии/адреса/коды.')],
'thousands-spacing':[(r'\b[1-9]\d{3,}\b','Разбить многозначное число пробелами, если это не год/идентификатор.')],
'percent-spacing':[(r'\d+(?:[,.]\d+)?%(?!\p{L})','Поставить неразрывный пробел перед %.')],
'specialty-dot':[(r'\b\d+\.\d+\.\d+\s+[А-ЯЁ]','После номера специальности поставить точку.')],
'method-tuning':[(r'\bнастройк\p{L}*\s+метод\p{L}*\b','Уточнить, что настраиваются параметры метода.')],
'method-behavior':[(r'\bповедени\p{L}*\s+метод\p{L}*\b','Переформулировать через свойства или результаты метода.')],
'performance-verb':[(r'\bулучшени\p{L}*\s+производительности\b','Использовать «повышение производительности».')],
'model-shows':[(r'\bмодель\s+показыва\p{L}*\b','Использовать точный глагол.')],
'tasks-solved':[(r'\b(?:выполнени\p{L}*|выполнить)\s+(?:этой\s+|данной\s+)?задач\p{L}*\b','Задачу решают, а не выполняют.')],
'analogovye':[(r'\bаналогов(?:ое|ые|ая|ого|ых)\s+решени\p{L}*\b','Использовать «аналогичные решения».')],
'roman-ending':[(r'\b[IVXLCDM]+-(?:ую|ой|го|я|е|й)\b','Не присоединять русское окончание к римской цифре.')],
'implemented-in-company':[(r'\bвнедр(?:ен|ена|ено|ены|ил|или)\p{L}*\s+в\s+компанию\b','Проверить нормативный предлог.')],
'formula-wording':[(r'\bв\s+соответствии\s+со\s+следующей\s+формулой\b','Сократить до «по формуле».')],
'next-respectively':[(r'\bследующ\p{L}*\b(?:(?![.!?]).){0,55}\bследующ\p{L}*\b','Убрать повтор слова «следующий».')],
'receiver-successor':[(r'\bприемник(?:а|ом|у)?\s+(?:президент|руководител|директор)','Использовать «преемник».')],
'colloquial-errors':[(r'\b(?:ложить|ложит|ложат|ихний|ихняя|слазить|слазя|влазить|залазить)\b','Заменить просторечную форму нормативной.')],
'present-work':[(r'\bнастоящая\s+работа\b','Сократить до «работа».')],
'format-parent-word':[(r'\b(?:SVG\s+и\s+PNG|PNG\s+и\s+SVG)\b','Добавить родовое слово «форматы».')],
'document-garbage':[(r'(?:Error!|\b\d{2,}[a-z]{3,}\d{4}[a-z]+\b|####+|<undefined>|\[object Object\])','Удалить технический мусор.')],
'transition-to-conclusions':[(r'\bперейд[её]м\s+к\s+изложению\s+выводов\b','Убрать фразу-переход.')],
'results-word':[(r'\bитоги\s+(?:главы|раздела|работы)\b','Использовать «выводы».')],
}


def _scope_blocks(document:dict,detector:str,rule:dict)->list[dict]:
    fields=document.get('fields',{})
    if detector in {'title-process','title-vague-efficiency','title-length'}: return [fields['title']] if fields.get('title') else []
    if detector=='goal-infinitive': return [fields['goal']] if fields.get('goal') else []
    if detector in {'initials-order','bibliography-junk','bibliography-pages'}: return fields.get('bibliographyBlocks',[])
    if detector=='heading-final-period':
        ids=main_work_ids(document); blocks=document.get('blocks',[])
        scoped=blocks if ids is None else [b for b in blocks if str(b.get('id')) in ids]
        return [b for b in scoped if b.get('type')=='heading' or is_actual_caption(b)]
    return narrative_blocks(document)


def _likely_hyphenated_name(value: str) -> bool:
    return bool(re.search(r'(?:end-to-end|out-of-domain|ToolRet-Web|Auto-GPT|API-Bank|Sentence-BERT|Qwen3-Embedding|Post-Selection|Retrieval–Plan–Select|TF–IDF|[A-Za-z]+-[A-Za-z0-9]+|«[^»]{1,80}(?:[А-ЯЁа-яё]+–){2,}[А-ЯЁа-яё]+[^»]{0,80}»)', value))


def _pdf_line_wrap(text: str, index: int) -> bool:
    around=text[max(0,index-35):min(len(text),index+35)]
    return bool(re.search(r'[А-ЯЁа-яё]-\s*\n\s*[А-ЯЁа-яё]',around))


def _noisy_match(rule_id: str, quote: str) -> bool:
    if is_code_or_prompt(quote):
        return True
    if rule_id == 'CORE-3-1' and is_likely_table_context(quote):
        return True
    return False


def _praise(rule, document):
    ev=[]
    pattern=re.compile(r'\b(?:уникальн\p{L}*|высокоэффективн\p{L}*|совершенно\s+бесспорн\p{L}*|значительн\p{L}*\s+вклад|наглядно\s+демонстрир\p{L}*)\b',re.I)
    for b in narrative_blocks(document):
        for m in pattern.finditer(b.get('text','')):
            q=contextual(b['text'],m.start(),len(m.group()))
            if re.search(r'числ[оа]\s+уникальн|уникальн\p{L}*\s+(?:текстов\p{L}*\s+)?(?:запрос|значени|идентификатор|объект|элемент|запис|класс)',q,re.I) or is_likely_table_context(q):
                continue
            ev.append(evidence(b,q))
    ev=dedupe_evidence(ev)[:12]
    if ev:
        return result(rule,'violation',rule.get('requirement','Необоснованная оценочная формулировка.'),ev,.99,'detector','Заменить оценку измеримым сравнением с прототипом или нейтральным утверждением.')
    return result(rule,'pass','Необоснованные восторженные оценки не обнаружены; статистические сочетания вроде «число уникальных запросов» исключены.',confidence=.99)

def _title_process(rule, document):
    title = document.get('fields', {}).get('title')
    if not title:
        return result(rule, 'uncertain', 'Название работы не удалось надёжно извлечь.', confidence=.2)
    text = str(title.get('text') or '').strip()
    match = re.match(r'^(разработка|исследование|изучение)\b', text, re.I)
    if not match:
        return result(
            rule, 'pass',
            'Название не начинается с процессуальных существительных «разработка», «исследование» или «изучение».',
            confidence=.99,
        )
    token = match.group(1)
    return result(
        rule, 'violation',
        f'Название начинается с процессуального существительного «{token}» и формулирует процесс, а не научный результат.',
        [evidence(title, text)], .99, 'detector',
        'Переформулировать название через научный результат (например, метод, модель, алгоритм, систему, оценку или иной фактически полученный результат).',
    )


def _title_length(rule,document):
    title=document.get('fields',{}).get('title')
    if not title: return result(rule,'uncertain','Название работы не удалось надёжно извлечь.',confidence=.2)
    words=re.findall(r'[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*',title.get('text',''))
    if len(words)<=13: return result(rule,'pass',f'В названии {len(words)} слов — не более 13.',confidence=.98)
    return result(rule,'violation',f'В названии {len(words)} слов, что превышает рекомендуемый предел 13.',[evidence(title,title['text'])],.99,'detector','Сократить название, сохранив объект и результат работы.')


def _personal(rule,document):
    ev=[]
    p=re.compile(r'(?<![\p{L}\p{N}_])(?:я|мы|наш(?:а|е|и|его|ему|им|ими)?|нами|мною|мой|моя|моё|мои)(?![\p{L}\p{N}_])',re.I)
    for b in narrative_blocks(document):
        text=b.get('text','')
        if re.search(r'\bАлгоритм\s*:',text,re.I) or formula_like_block(text):
            continue
        for m in p.finditer(text):
            token=m.group().lower()
            prefix=text[max(0,m.start()-24):m.start()]
            if token in {'нами','мною'} and re.search(r'[\p{L}]\u00ad?\s*$',prefix):
                continue
            q=contextual(text,m.start(),len(m.group()))
            if is_likely_table_context(q):
                continue
            ev.append(evidence(b,q))
    ev=dedupe_evidence(ev)[:12]
    return result(rule,'violation',rule.get('requirement','Личные местоимения не рекомендуются.'),ev,.99,'detector','Переформулировать безлично: «в работе предложено», «автором разработано» — если это не искажает смысл.') if ev else result(rule,'pass','В русскоязычном основном тексте не обнаружены отдельные формы «я», «мы», «наш». Составные слова и переносы PDF исключены.',confidence=.99)


def _yo(rule,document):
    # Keep this detector deliberately high-precision. In particular, short passive
    # forms such as «проведено/проведены» and nouns such as «проведение» contain
    # е, not ё; the old broad stems produced systematic false positives.
    candidates=[
        (r'\bза\s+счет\b','за счёт'),
        (r'\bвсе\s+еще\b','всё ещё'),
        (r'\bеще\b','ещё'),
        (r'\bучет(?:а|е|ом|у|ы|ов)?\b','учёт…'),
        (r'\bобъем(?:а|е|ом|у|ы|ов)?\b','объём…'),
        (r'\bприем(?:а|е|ом|у|ы|ов)?\b','приём…'),
        (r'\bнадежн\p{L}*\b','надёжн…'),
        (r'\bпроведен(?:\b|н(?:ый|ая|ое|ые|ого|ому|ым|ыми|ых|ой|ую)\b)','проведён…'),
        (r'\bподтвержден(?:\b|н(?:ый|ая|ое|ые|ого|ому|ым|ыми|ых|ой|ую)\b)','подтверждён…'),
    ]
    ev=[]
    for b in narrative_blocks(document):
        for p,_ in candidates:
            for m in re.finditer(p,b['text'],re.I): ev.append(evidence(b,contextual(b['text'],m.start(),len(m.group()))))
    ev=dedupe_evidence(ev)[:12]
    return result(rule,'violation','Обнаружены высокоуверенные слова, где требуется буква «ё».',ev,.99,'detector','Исправить конкретные найденные слова.') if ev else result(rule,'pass','Высокоуверенные замены е/ё не обнаружены.',confidence=.98)


def _quote_consistency(rule,document):
    variants={}
    for b in narrative_blocks(document):
        text=b['text']
        for name,p in [('ёлочки',r'«[^»]{2,}»'),('английские',r'“[^”]{2,}”'),('немецкие',r'„[^“]{2,}“'),('прямые двойные',r'"[^"\n]{2,}"')]:
            m=re.search(p,text)
            if m and name not in variants:
                q=contextual(text,m.start(),len(m.group()))
                if name=='прямые двойные':
                    if is_code_or_prompt(q):
                        continue
                    around=text[max(0,m.start()-100):min(len(text),m.end()+100)]
                    numbers=len(re.findall(r'(?<!\p{L})\d+(?:[.,]\d+)?%?',around))
                    prose_words=len(re.findall(r'[А-ЯЁа-яё]{3,}',around))
                    if numbers >= 10 and numbers >= prose_words * 1.2:
                        continue
                variants[name]=evidence(b,q)
    if len(variants)<=1: return result(rule,'pass','В основном тексте не обнаружено смешения нескольких типов кавычек.',confidence=.98)
    return result(rule,'violation','Обнаружено несколько типов кавычек: '+', '.join(variants)+'.',list(variants.values()),.99,'detector','Выбрать один тип кавычек.')


def _starts_numbered_item(text: str) -> bool:
    return bool(re.match(r'^\s*(?:\(\d{1,3}\)|\d{1,3}[.)])\s+', str(text or '')))


def _list_groups(document: dict) -> list[list[dict]]:
    """Return conservative contiguous numbered-list regions.

    A PDF list item can continue in an ordinary paragraph block on the next
    page.  Checking each block independently turns such continuations into
    false punctuation violations.  Group only blocks that are explicitly
    typed as lists, plus a paragraph continuation when the previous list block
    visibly ends mid-sentence.  Headings/formulas/captions terminate a group.
    """
    scope_ids = main_work_ids(document)
    # Preserve every source block as a potential boundary.  Do not reuse the
    # Cyrillic-language filter from ``narrative_blocks`` here: a Russian list
    # item may continue with an English title (for example a publication), and
    # dropping that continuation falsely makes the previous block look like an
    # unfinished item.
    blocks = list(document.get('blocks') or [])
    groups: list[list[dict]] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        text = str(block.get('text') or '')
        block_id = str(block.get('id'))
        in_scope = scope_ids is None or block_id in scope_ids
        if (not in_scope or block.get('type') != 'list' or not _starts_numbered_item(text)
                or looks_like_contents(text) or is_code_or_prompt(text)):
            i += 1
            continue
        group = [block]
        j = i + 1
        while j < len(blocks):
            nxt = blocks[j]
            nxt_text = str(nxt.get('text') or '')
            nxt_id = str(nxt.get('id'))
            if scope_ids is not None and nxt_id not in scope_ids:
                break
            if looks_like_contents(nxt_text) or is_code_or_prompt(nxt_text):
                break
            if nxt.get('type') == 'list' and _starts_numbered_item(nxt_text):
                group.append(nxt)
                j += 1
                continue
            prev_text = str(group[-1].get('text') or '').rstrip()
            # PyMuPDF can split one list item at a page boundary.  A paragraph
            # immediately after an unfinished list block is part of that item.
            if (nxt.get('type') == 'paragraph'
                    and prev_text
                    and not re.search(r'[.!?;:]\s*$', prev_text)
                    and not looks_like_contents(nxt_text)):
                group.append(nxt)
                j += 1
                continue
            break
        groups.append(group)
        i = max(j, i + 1)
    return groups


def _item_evidence(item: dict, *, ending: bool = False) -> dict:
    source = item.get('endSource') if ending else item.get('source')
    source = source or item.get('source') or item.get('block') or {}
    source_blocks = item.get('sourceBlockIds') or []
    full = str(item.get('full') or item.get('body') or '')
    # A multi-block quote does not literally occur inside either source block.
    # Keep evidence grounded by quoting the actual boundary block instead.
    quote = full if len(source_blocks) <= 1 and full in str(source.get('text') or '') else str(source.get('text') or '')
    return evidence(source, quote)


def _math_property_item(item: dict) -> bool:
    """Exclude numbered mathematical identities from prose-list punctuation rules."""
    body = str(item.get('body') or '').strip()
    head = body[:120]
    return bool(
        re.search(r'[=≈≤≥∈∉⊂∑∫√]', head)
        and (re.match(r'^[^А-ЯЁа-яё]{0,8}[A-Za-zΑ-Ωα-ωΦφΘθΛλ𝒜-𝓏]', head) or len(re.findall(r'[=≈≤≥∈∉⊂∑∫√]', head)) >= 2)
    )


def _bibliographic_item(item: dict) -> bool:
    body = str(item.get('body') or '')
    return bool(re.search(r'\bdoi\s*:|https?://|\bISBN\b|\s//\s', body, re.I))


_NON_SENTENCE_DOT_TAIL = re.compile(
    r'(?:\b(?:т|т\s*е|т\s*к|т\s*ч|т\s*д|т\s*п|рис|табл|стр|гл|им|см|англ|лат|ред|др|проф|доц|акад|и\s*т\s*д|и\s*т\s*п)\.|'
    r'\b[А-ЯЁA-ZА-ЯЁа-яё]\.|\b(?:п|г|гг|ст|науч|сотр|рис|табл|стр|гл|им|см|англ|лат|ред|др|проф|доц|акад)\.|\b(?:e\.g|i\.e)\.)\s*$', re.I
)


def _lowercase_after_sentence_dot(block: dict) -> list[dict]:
    """Find only high-confidence sentence boundaries followed by lowercase.

    We deliberately exclude abbreviations/initials.  This extends CORE-3-3 to
    ordinary prose without bringing back the old false positive for ``1)``.
    """
    text=str(block.get('text') or '')
    found=[]
    for match in re.finditer(r'(?P<dot>[.!?])(?P<space>\s+)[«"“(]*?(?P<letter>[а-яё])', text):
        prefix=text[max(0, match.start()-28):match.start()+1]
        if match.group('dot') == '.' and _NON_SENTENCE_DOT_TAIL.search(prefix):
            continue
        right=text[match.end('space'):match.end('space')+24]
        # A lowercase abbreviation such as ``п. 8`` immediately after a
        # sentence-ending full stop is not itself a lowercase sentence start.
        if re.match(r'[«"“(]*(?:п|г|гг|ст|рис|табл|стр|гл|им|см|англ|лат|ред|др|проф|доц|акад)\.(?:\s|\d)', right, re.I):
            continue
        # Decimal/version/citation punctuation is not a sentence boundary.
        if re.search(r'\d\.\s*$', prefix) and re.search(r'\d', text[max(0,match.start()-4):match.start()]):
            continue
        quote=contextual(text, match.start(), len(match.group(0)), before=90, after=100)
        if formula_like_block(quote) or is_likely_table_context(quote):
            continue
        found.append(evidence(block, quote))
    return found


def _list_cap(rule,document):
    """CORE-3-3: check list-dot markers *and* all prose sentence boundaries."""
    ev=[]
    for group in _list_groups(document):
        for item in collect_numbered_items(group):
            if item.get('markerKind') != 'dot':
                continue
            body=str(item.get('body') or '').lstrip(' «"“(').lstrip()
            if not body or not re.match(r'^[а-яё]',body):
                continue
            if _math_property_item(item) or _bibliographic_item(item):
                continue
            ev.append(_item_evidence(item))
    for block in narrative_blocks(document):
        ev.extend(_lowercase_after_sentence_dot(block))
    ev=dedupe_evidence(ev)[:12]
    if ev:
        return result(rule,'violation','После точки/границы предложения обнаружено начало со строчной буквы.',ev,1,'detector','После точки начать новое предложение с прописной буквы.')
    return result(rule,'pass','Во всей назначенной текстовой области не обнаружены высокоуверенные случаи строчной буквы после точки/границы предложения.',confidence=1)

def _forbidden_abbreviations(rule,document):
    """Check CORE-11-3 without confusing ordinary word endings with ``т. е.``.

    The old pattern made the dot after ``т`` optional, so a PDF word such as
    ``спли-те.`` could expose the substring ``те.`` and become a false positive.
    The normative forms always contain the first full stop, therefore requiring
    it is both stricter and more faithful to the rule.
    """
    forbidden=re.compile(r'(?<![\p{L}\p{N}_])т\.\s*(?:е|к|ч)\.(?!\p{L})',re.I)
    bad_spacing=re.compile(r'(?<![\p{L}\p{N}_])т\.[дп]\.(?!\p{L})',re.I)
    ev=[]
    for b in narrative_blocks(document):
        text=b.get('text','')
        for pattern in (forbidden,bad_spacing):
            for m in pattern.finditer(text):
                # ``Т. Е. Иванов`` is a sequence of initials, not the
                # abbreviation ``т. е.``.  Preserve it when a surname follows.
                right=text[m.end():m.end()+48]
                if re.match(r'\s*[А-ЯЁ][а-яё-]{2,}',right):
                    continue
                ev.append(evidence(b,contextual(text,m.start(),len(m.group()))))
    ev=dedupe_evidence(ev)[:12]
    if ev:
        return result(rule,'violation',rule.get('requirement','Обнаружено запрещённое сокращение.'),ev,1,'detector','Раскрыть «т. е.», «т. к.», «т. ч.» словами; «т. д.» и «т. п.» писать с пробелами.')
    return result(rule,'pass','Запрещённые сокращения «т. е.», «т. к.», «т. ч.» и слитные «т.д.», «т.п.» не обнаружены.',confidence=1)


def _list_ending(rule,document,numbered=True):
    ev=[]
    if numbered:
        # Parse a structural list as a region rather than treating every PDF
        # block as a complete item. This keeps page-split items together.
        for group in _list_groups(document):
            if any(re.search(r'\bАлгоритм\s*:', str(b.get('text') or ''), re.I) for b in group):
                continue
            for item in collect_numbered_items(group):
                if _math_property_item(item) or _bibliographic_item(item):
                    continue
                body=str(item.get('body') or '').rstrip()
                bad_end = not body.endswith('.')
                bad_cap = rule['id']=='SOFT-030' and not re.match(r'^\p{Lu}', body.lstrip())
                if bad_end or bad_cap:
                    ev.append(_item_evidence(item, ending=True))
    else:
        for b in narrative_blocks(document):
            if re.search(r'\bАлгоритм\s*:',b.get('text',''),re.I) or formula_like_block(b.get('text','')):
                continue
            for line in b['text'].splitlines():
                line=line.strip()
                if re.match(r'^(?:[-–—•]|\d+\))\s+',line) and not re.search(r'[.;]$',line):
                    ev.append(evidence(b,line))
    return result(rule,'violation','Обнаружено нарушение окончания пункта списка.',dedupe_evidence(ev)[:12],1,'detector','Исправить окончания пунктов.') if ev else result(rule,'pass','Явных нарушений окончания пунктов не обнаружено.',confidence=1)

def _small_numerals(rule,document):
    patterns=[
        re.compile(r'(?<![\d.,])(?<![\p{L}\p{N}_])[0-9]\s+(?:рабо(?:-\s*)?т\p{L}*|публикаци\p{L}*|модел\p{L}*|метод\p{L}*|вопрос\p{L}*|конфигураци\p{L}*|бенчмарк\p{L}*|задач\p{L}*|положени\p{L}*|этап\p{L}*|принцип\p{L}*|вариант\p{L}*|категори\p{L}*|глав\p{L}*)(?![\p{L}\p{N}_])',re.I),
        re.compile(r'(?:из\s+них|из\s+которых|а|и)\s+[0-9]\s+(?:опубликован\p{L}*|подан\p{L}*|принят\p{L}*|представлен\p{L}*|использован\p{L}*)(?![\p{L}\p{N}_])',re.I),
    ]
    ev=[]
    for b in narrative_blocks(document):
        if b.get('type') in {'formula','caption'} or looks_like_contents(b.get('text','')): continue
        for pattern in patterns:
            for m in pattern.finditer(b.get('text','')):
                q=contextual(b['text'],m.start(),len(m.group()))
                prefix=b['text'][max(0,m.start()-24):m.start()]
                if re.search(r'(?:глав(?:а|е|ы)|раздел(?:е|а)|рисунк(?:е|а)|таблиц(?:е|ы))\s*$',prefix,re.I) or is_likely_table_context(q) or re.search(r'\bpass\s*\d|\d\s*pass\b',q,re.I): continue
                ev.append(evidence(b,q))
                if len(ev)>=12: break
            if len(ev)>=12: break
        if len(ev)>=12: break
    ev=dedupe_evidence(ev)[:12]
    return result(rule,'violation','В связном тексте обнаружено числительное от нуля до девяти, записанное цифрой в высокоуверенном языковом контексте.',ev,1,'detector','Записать числительное словом либо подтвердить, что это специальное обозначение.') if ev else result(rule,'pass','Высокоуверенные случаи записи числительных от нуля до девяти цифрами не обнаружены.',confidence=1)


def _defense(rule,document,punctuation=True):
    items=collect_unique_defense_items(document.get('fields',{}).get('defenseStatements',[]))
    if not items: return result(rule,'uncertain','Положения на защиту не удалось выделить как целые нумерованные пункты.')
    ev=[]
    if punctuation:
        for item in items:
            text=item['text'].strip()
            if not re.match(r'^\p{Lu}',text) or not text.endswith('.') or text.endswith(';'):
                ev.append(evidence(item['source'],f"{item['number']}. {text}"))
        return result(rule,'violation','Найдено положение с неверной прописной буквой или завершающим знаком.',dedupe_evidence(ev)[:12],1,'detector','Начать положение с прописной буквы и завершить точкой.') if ev else result(rule,'pass',f'Все {len(items)} распознанных положений начинаются с прописной буквы и заканчиваются точкой.',confidence=1)
    from ..document.fact_store import notation_classification
    for item in items:
        text = item['text']
        symbols = bool(re.search(r'[=≈≤≥∑∫√±∞∈∉⊂]|\\(?:frac|sum|int)\b', text))
        raw_tokens = re.findall(r'(?<![\p{L}\p{N}_])[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё0-9@_-]+(?![\p{L}\p{N}_])', text)
        tokens = [token for token in raw_tokens if token.isupper() or sum(ch.isupper() for ch in token) >= 2]
        listed = any(notation_classification(document.get('factStore'), token) == 'yes' for token in tokens)
        if symbols or listed:
            ev.append(evidence(item['source'], f"{item['number']}. {text}"))
    if ev:
        return result(rule, 'violation', 'В положениях найдены явные математические символы или сокращения из общей карты обозначений.',
                      dedupe_evidence(ev)[:12], .98, 'detector', 'Раскрыть обозначения словами.')
    # No symbolic candidate at all is itself exhaustive deterministic evidence.
    # If candidates exist, only unresolved classifications keep the rule manual.
    unresolved=[]
    for item in items:
        for token in re.findall(r'(?<![\p{L}\p{N}_])[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё0-9@_-]+(?![\p{L}\p{N}_])', item['text']):
            if not (token.isupper() or sum(ch.isupper() for ch in token) >= 2):
                continue
            state=notation_classification(document.get('factStore'), token)
            if state not in {'no', 'not_applicable'}:
                unresolved.append(token)
    if unresolved:
        return result(rule, 'uncertain', 'В положениях остаются обозначения с неоднозначной классификацией: ' + ', '.join(sorted(set(unresolved))) + '.')
    return result(rule, 'pass', f'Все {len(items)} положения полностью просмотрены: аббревиатуры, математические обозначения и формулы не обнаружены.', confidence=1)


def _block_has_rich_pdf_layout(block: dict) -> bool:
    lines=block.get('lines')
    return bool(isinstance(lines,list) and any(isinstance(line,dict) and line.get('spans') for line in lines))


def _rich_pdf_layout_coverage(document: dict) -> float:
    blocks=narrative_blocks(document)
    total=sum(max(1,len(str(b.get('text') or ''))) for b in blocks)
    rich=sum(max(1,len(str(b.get('text') or ''))) for b in blocks if _block_has_rich_pdf_layout(b))
    return rich / total if total else 0.0


def _spacing(rule,document):
    initials=[]; percents=[]
    for b in narrative_blocks(document):
        for m in re.finditer(r'(?<![\p{L}\p{N}_])[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]\.(?=[А-ЯЁA-Z][а-яёa-z-])',b['text']): initials.append(evidence(b,contextual(b['text'],m.start(),len(m.group()))))
        for m in re.finditer(r'\d+(?:[,.]\d+)?%(?!\p{L})',b['text']):
            q=contextual(b['text'],m.start(),len(m.group()))
            if not is_likely_table_context(q): percents.append(evidence(b,q))
    if initials: return result(rule,'violation','Обнаружено написание инициалов без пробела перед фамилией.',dedupe_evidence(initials)[:12],1,'detector','Добавить пробел.')
    if percents:
        if document.get('sourceFormat')!='pdf' or _rich_pdf_layout_coverage(document) >= .85:
            return result(rule,'violation','Обнаружено число без пробела перед знаком процента.',dedupe_evidence(percents)[:12],1,'detector','Добавить неразрывный пробел.')
        return result(rule,'uncertain','В legacy PDF знак процента местами прилегает к числу, но геометрия пробелов недоступна.',dedupe_evidence(percents)[:8],0,'detector')
    dash=_dash(rule, document)
    if dash.get('status')=='pass' and document.get('sourceFormat')=='pdf' and _rich_pdf_layout_coverage(document) < .85:
        return result(rule,'uncertain','Явных нарушений пробелов не найдено, но legacy PDF не содержит достаточно геометрии для полного подтверждения.',confidence=0)
    return dash


def _dash(rule,document):
    ev=[]
    patterns=[r'(?<=\p{L})\s-\s(?=\p{L})',r'(?<=\p{L})\s+[—–](?=\p{L})',r'(?<=\p{L})[—–]\s+(?=\p{L})',r'(?<=\p{L})--(?=\p{L})']
    for b in narrative_blocks(document):
        for p in patterns:
            for m in re.finditer(p,b['text']):
                q=contextual(b['text'],m.start(),len(m.group()))
                if _likely_hyphenated_name(q) or _pdf_line_wrap(b['text'],m.start()): continue
                ev.append(evidence(b,q))
    ev=dedupe_evidence(ev)[:12]
    if not ev: return result(rule,'pass','Высокоуверенные нарушения различия тире и дефиса не обнаружены.',confidence=.98)
    if document.get('sourceFormat')=='pdf' and _rich_pdf_layout_coverage(document) < .85:
        return result(rule,'uncertain','В legacy PDF найдены возможные нарушения тире, но геометрия пробелов недоступна.',ev,0,'detector')
    return result(rule,'violation','Обнаружено тире без требуемых пробелов либо дефис вместо тире; пробелы подтверждены структурой исходного документа.',ev,1,'detector','Использовать среднее тире «–» с пробелами.')


def _generic(rule,document,detector):
    specs=SPECS.get(detector)
    if rule['id'] == 'SOFT-038':
        specs = [*SPECS['lexical-replacements'][:2], (r'\bочевидно\b', 'Убрать слово «очевидно».')]
    if rule['id'] == 'SOFT-045':
        specs = [(r'\bвышеизложенн\p{L}*\b', 'Заменить на «изложенное выше».')]
    if not specs: return result(rule,'not_checked',f'Детектор {detector} ещё не реализован.')
    ev=[]; fix=None
    for b in _scope_blocks(document,detector,rule):
        for pattern,pattern_fix in specs:
            for m in re.finditer(pattern,b.get('text',''),re.I|re.M):
                # Conservative exclusions for common numeric false positives.
                q=contextual(b['text'],m.start(),len(m.group()))
                if detector == 'format-parent-word' and re.search(r'\bформат\p{L}*\s*$', b['text'][:m.start()], re.I):
                    continue
                if _noisy_match(rule.get('id',''),q): continue
                if detector == 'obvious-claims':
                    left = b.get('text','')[max(0, m.start() - 36):m.start()]
                    if re.search(r'\bне\s*$', left, re.I):
                        continue
                if detector=='decimal-comma' and (re.search(r'\b(?:v?\d+\.\d+\.\d+|\d{1,3}(?:\.\d{1,3}){3})\b',q,re.I) or re.search(r'\b(?:рис|табл|гл)\.\s*\d+\.\d+',q,re.I)): continue
                if detector=='thousands-spacing' and re.search(r'\b(?:19|20)\d{2}\b',m.group()): continue
                ev.append(evidence(b,q)); fix=fix or pattern_fix
    ev=dedupe_evidence(ev)[:12]
    return result(rule,'violation',rule.get('requirement','Обнаружено нарушение.'),ev,.98,'detector',fix) if ev else result(rule,'pass','Высокоуверенные совпадения для данного правила не обнаружены.',confidence=.98)


@grounded_check
def run_deterministic(rule:dict,document:dict)->dict:
    detector=rule.get('detectorId') or RULE_FALLBACK.get(rule['id'])
    if rule['id'] in {'CORE-9-2','CORE-9-3'}: return run_bibliography_rule(rule,document)
    if not detector: return result(rule,'not_checked','Для правила не назначен детерминированный детектор.')
    if detector=='title-process': return _title_process(rule,document)
    if detector=='title-length': return _title_length(rule,document)
    if detector=='personal-pronouns': return _personal(rule,document)
    if detector=='praise-claims': return _praise(rule,document)
    if detector=='yo-letter': return _yo(rule,document)
    if detector=='quote-consistency': return _quote_consistency(rule,document)
    if detector=='numbered-list-ending': return _list_ending(rule,document,True)
    if detector=='bullet-list-ending': return _list_ending(rule,document,False)
    if detector=='numbered-list-capitalization': return _list_cap(rule,document)
    if detector=='forbidden-abbreviations': return _forbidden_abbreviations(rule,document)
    if detector=='small-numerals': return _small_numerals(rule,document)
    if detector=='defense-punctuation': return _defense(rule,document,True)
    if detector=='defense-symbols': return _defense(rule,document,False)
    if detector=='spacing': return _spacing(rule,document)
    if detector=='dash-spacing': return _dash(rule,document)
    return _generic(rule,document,detector)
