from __future__ import annotations

import csv
import re
from functools import lru_cache
from io import StringIO
from typing import Any

from ..config import RULES_DIR

SCOPE_OVERRIDES = {
    'core:1.6':'defense_statements','core:2.1':'document','core:5.1':'document','core:5.2':'document',
    'core:7.3':'figure_table','core:8.3':'document','core:14':'document','core:15':'chapter','core:16':'document',
}
MODE_OVERRIDES = {
    'core:1.6':'structural','core:2.1':'semantic','core:5.1':'manual','core:5.2':'manual',
    'core:4.1':'semantic','core:4.2':'semantic','core:4.3':'semantic','core:12':'semantic',
    'core:7.3':'manual','core:8.2':'semantic','core:8.3':'semantic','core:14':'semantic','core:15':'semantic','core:16':'manual',
}
STRUCTURAL_RULES = {
    'core:1.6','core:4.1','core:4.2','core:4.3','core:5.3','core:5.4','core:7.1','core:7.2','core:7.4','core:7.5','core:8.1','core:8.3','core:9.1','core:9.4','core:12','core:13','core:14','core:15','core:18','core:19',
    *{f'soft:{n}' for n in [7,8,9,10,11,12,13,14,15,16,18,23,25,26,49,52,54,55,56,57,62,64,65,66,67,68,69,70,71,72,73,77,78,79,95,96,97,98,100,103,105,110,120,121,123,128,129,135,139,140,141,143,152,155,160]},
}
DETECTOR_BY_RULE = {
    'core:3.1':'personal-pronouns','core:3.2':'small-numerals','core:3.3':'numbered-list-capitalization','core:3.4':'numbered-list-ending',
    'core:3.5':'lexical-replacements','core:3.6':'obvious-claims','core:3.7':'praise-claims','core:5.5':'dash-spacing','core:5.6':'quote-consistency','core:5.7':'spacing',
    'core:6.1':'title-process','core:6.2':'title-vague-efficiency','core:6.3':'title-length','core:9.2':'bibliography-junk','core:9.3':'bibliography-pages',
    'core:11.1':'yo-letter','core:11.2':'forbidden-sentence-start','core:11.3':'forbidden-abbreviations','core:11.4':'colon-a-imenno','core:11.5':'to-est','core:11.6':'diminutives','core:17':'see-figure','core:20':'first-new',
    'soft:27':'personal-pronouns','soft:28':'numbered-list-capitalization','soft:29':'heading-final-period','soft:30':'numbered-list-ending','soft:31':'bullet-list-ending','soft:32':'dash-spacing','soft:34':'quote-consistency','soft:35':'small-numerals','soft:37':'yo-letter','soft:38':'lexical-replacements','soft:39':'praise-claims','soft:40':'diminutives','soft:42':'forbidden-sentence-start','soft:44':'colon-and-to-est','soft:45':'above-written','soft:46':'lexical-cliches','soft:47':'jargon','soft:48':'first-new','soft:50':'title-process','soft:51':'title-length','soft:53':'goal-infinitive','soft:59':'python-capitalization','soft:60':'initials-order','soft:74':'decimal-comma','soft:75':'thousands-spacing','soft:76':'percent-spacing','soft:121':'specialty-dot','soft:124':'method-tuning','soft:125':'method-behavior','soft:126':'performance-verb','soft:127':'model-shows','soft:130':'tasks-solved','soft:132':'analogovye','soft:134':'roman-ending','soft:137':'implemented-in-company','soft:138':'yo-letter','soft:144':'formula-wording','soft:145':'next-respectively','soft:146':'receiver-successor','soft:147':'colloquial-errors','soft:149':'present-work','soft:152':'format-parent-word','soft:155':'document-garbage','soft:159':'transition-to-conclusions','soft:161':'results-word',
}

CANDIDATE_FAMILY_BY_RULE = {
    'core:3.1':'impersonal',
    'core:3.2':'numerals',
    'core:3.5':'filler',
    'core:3.6':'condescending',
    'core:3.7':'overclaim',
    'core:11.2':'sentence-start',
    'core:11.5':'to-est',
    'core:11.6':'diminutive',
    # Exact soft duplicates reuse the same candidates in the full profile.
    'soft:27':'impersonal',
    'soft:35':'numerals',
    'soft:39':'overclaim',
    'soft:40':'diminutive',
    'soft:42':'sentence-start',
}
DEDUP_BY_RULE = {
    'core:1.1':'defense-statement-form',
    'core:1.2':'defense-statement-form',
    'core:1.3':'defense-statement-form',
    'core:4.1':'abbreviation-introduction',
    'core:12':'abbreviation-introduction',
    'core:4.2':'abbreviation-heading',
    'core:4.3':'abbreviation-translation',
    'core:5.4':'caption-terminal-punctuation',
    'core:7.2':'caption-terminal-punctuation',
    'core:19':'caption-terminal-punctuation',
    'core:3.1':'impersonal-style',
    'core:3.2':'small-numerals',
    'core:3.5':'filler-language',
    'core:3.6':'condescending-language',
    'core:3.7':'evaluative-language',
    'core:11.2':'sentence-start',
    'core:11.5':'to-est',
    'core:11.6':'diminutive-language',
    'soft:27':'impersonal-style',
    'soft:35':'small-numerals',
    'soft:39':'evaluative-language',
    'soft:40':'diminutive-language',
    'soft:42':'sentence-start',
}

STOP_WORDS = {'это','как','что','для','или','при','также','так','все','всех','его','ее','она','они','над','под','без','после','перед','если','где','когда','который','которые','данных','работы','работа','главе','глава','раздел','текст','текста','тексте','быть','может','должен','должна','должны','было','были','будет','будут','чтобы','этого','этой','этих','этом','такой','такие','такого','требование','совет'}


def tokenize(value: str) -> list[str]:
    parts = re.sub(r'[^0-9A-Za-zА-Яа-яЁё]+', ' ', value.lower().replace('ё','е')).split()
    return list(dict.fromkeys(p for p in parts if len(p) >= 3 and p not in STOP_WORDS))


def _scope(category: str, requirement: str, key: str) -> str:
    if key in SCOPE_OVERRIDES: return SCOPE_OVERRIDES[key]
    value = f'{category} {requirement}'.lower()
    if re.search(r'презентац|слайд', value): return 'presentation'
    if re.search(r'доклад|вопрос|совет|защит[аеу]|одежд|паспорт|говорить|не читать', value) and not re.search(r'положени.*защит', value): return 'defense'
    if re.search(r'процесс работы|вылежаться|руководител|word|pdf|llm|итерац', value): return 'process'
    if key in {'core:6.1','core:6.2','core:6.3'} or re.match(r'soft:(49|50|51|52)$', key): return 'title'
    if key in {'core:6.4','soft:53'}: return 'goal'
    if re.search(r'положени.*выносим', value): return 'defense_statements'
    if re.search(r'список литератур|библиограф|источник', value): return 'bibliography'
    if 'формул' in value: return 'formula'
    if re.search(r'рисунк|таблиц|график|подрисуноч|оси', value): return 'figure_table'
    if re.search(r'нумерованн|ненумерованн|пункт.*списк', value): return 'list'
    if 'глав' in value: return 'chapter'
    return 'document'


def _severity(category: str, requirement: str, key: str) -> str:
    value = f'{category} {requirement}'.lower()
    if re.search(r'положени.*защит|научн.*новизн|прототип|цель.*диссертац|диссертац.*научн', value): return 'critical'
    if re.search(r'структур|глава|вывод|заключени|внедрен|названи|ссылка.*рис|список литератур', value): return 'major'
    if re.search(r'пробел|кавыч|тире|дефис|буква ё|точк|числитель|жаргон|слово|инициал|процент|дроб', value): return 'minor'
    if _scope(category, requirement, key) in {'presentation','defense','process'}: return 'info'
    return 'major'


def _meta(layer: str, number: str, category: str, requirement: str) -> dict[str, Any]:
    key = f'{layer}:{number}'
    detector = DETECTOR_BY_RULE.get(key)
    scope = _scope(category, requirement, key)
    candidate_family = CANDIDATE_FAMILY_BY_RULE.get(key)
    mode = 'candidate' if candidate_family else MODE_OVERRIDES.get(key) or ('deterministic' if detector else 'structural' if key in STRUCTURAL_RULES else 'manual' if scope in {'presentation','defense','process'} else 'semantic')
    sev = _severity(category, requirement, key)
    dedup_key = DEDUP_BY_RULE.get(key) or (f'detector:{detector}' if detector else key)
    return {
        'mode':mode,
        'scope':scope,
        'severity':sev,
        'weight':{'critical':5,'major':3,'minor':1,'info':0.25}[sev],
        'dedupKey':dedup_key,
        **({'candidateFamily':candidate_family} if candidate_family else {}),
        **({'detectorId':detector} if detector and not candidate_family else {}),
    }


def _title(requirement: str) -> str:
    compact = re.sub(r'\s+', ' ', requirement).strip()
    sentence = re.split(r'[.!?](?:\s|$)', compact)[0] or compact
    return sentence if len(sentence) <= 92 else sentence[:89].rstrip() + '…'


def _empty(value: str | None):
    value = (value or '').strip()
    return value if value and value != '–' else None


def _read_rows(path, delimiter: str) -> list[list[str]]:
    text = path.read_text(encoding='utf-8-sig')
    return list(csv.reader(StringIO(text), delimiter=delimiter, quotechar='"'))

@lru_cache(maxsize=1)
def load_rule_registry() -> dict[str, list[dict[str, Any]]]:
    core_rows = _read_rows(RULES_DIR / 'core.csv', ',')
    soft_rows = _read_rows(RULES_DIR / 'soft.csv', ';')
    core: list[dict[str, Any]] = []
    for idx, row in enumerate(core_rows[1:], start=2):
        if len(row) < 3 or not row[1].strip(): continue
        number, category, requirement = row[1].strip(), re.sub(r'^Группа\s+\d+\.\s*','',row[0],flags=re.I).strip(), row[2].strip()
        rid = 'CORE-' + re.sub(r'[^0-9A-Za-zА-Яа-яЁё]+','-',number).strip('-')
        item = {'id':rid,'sourceNumber':number,'category':category,'title':_title(requirement),'requirement':requirement,'sourceLabel':'ShalytoAI_csv_rules.txt','sourceLine':idx,'layer':'core',**_meta('core',number,category,requirement),'keywords':tokenize(f'{category} {requirement} {row[4] if len(row)>4 else ""}')}
        if len(row)>3 and _empty(row[3]): item['correctExample']=_empty(row[3])
        if len(row)>4 and _empty(row[4]): item['incorrectExample']=_empty(row[4])
        core.append(item)
    soft: list[dict[str, Any]] = []
    for idx, row in enumerate(soft_rows[1:], start=2):
        if len(row) < 3 or not row[0].strip(): continue
        number, category, requirement = row[0].strip(), row[1].strip(), row[2].strip()
        item = {'id':f'SOFT-{number.zfill(3)}','sourceNumber':number,'category':category,'title':_title(requirement),'requirement':requirement,'sourceLabel':'ShalytoAI_csv_rules(soft).txt','sourceLine':idx,'layer':'soft',**_meta('soft',number,category,requirement),'keywords':tokenize(f'{category} {requirement} {row[3] if len(row)>3 else ""}')}
        if len(row)>3 and _empty(row[3]): item['incorrectExample']=_empty(row[3])
        soft.append(item)
    return {'core':core,'soft':soft,'all':[ *core, *soft ]}


def parse_user_rules(value: str) -> list[dict[str, Any]]:
    result=[]
    for idx, requirement in enumerate([x.strip() for x in value.splitlines() if len(x.strip())>=8][:30], start=1):
        result.append({'id':f'USR-{idx:02d}','sourceNumber':str(idx),'category':'Дополнительные требования','title':_title(requirement),'requirement':requirement,'sourceLabel':'Пользовательские требования','sourceLine':idx,'layer':'user','mode':'semantic','scope':'document','severity':'major','weight':3,'dedupKey':f'user:{idx}','keywords':tokenize(requirement)})
    return result


def rules_for_profile(profile: str, additional: str='') -> list[dict[str, Any]]:
    registry=load_rule_registry(); base=registry['all'] if profile=='full' else registry['core']
    return [*base,*parse_user_rules(additional)]
