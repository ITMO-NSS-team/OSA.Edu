from __future__ import annotations
import regex as re
from .common import (evidence, contextual, dedupe_evidence, narrative_blocks, result, is_actual_caption,
    is_code_or_prompt, formula_like_block, is_likely_table_context, looks_like_contents)
from .bibliography import run_bibliography_rule
from ..document.numbered_items import extract_numbered_items, collect_unique_defense_items
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
'forbidden-abbreviations':[(r'\b(?:т\.?\s*е\.|т\.?\s*к\.|т\.?\s*ч\.)','Раскрыть сокращение.'),(r'\bт\.[дп]\.(?!\s)','Добавить пробел: «т. д.», «т. п.».')],
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
'implemented-in-company':[(r'\bвнедр(?:ен|ена|ено|ены|ил|или)\p{L}*\s+в\s+компани(?:ю|и)\b','Проверить нормативный предлог.')],
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


def _list_cap(rule,document):
    ev=[]
    for b in narrative_blocks(document):
        text=b.get('text','')
        if re.search(r'\bАлгоритм\s*:',text,re.I) or formula_like_block(text): continue
        for m in re.finditer(r'(?:^|\n)\s*\d{1,3}\.\s+([а-яё])',text,re.M):
            q=contextual(text,m.start(),len(m.group())+80)
            if is_likely_table_context(q): continue
            ev.append(evidence(b,q))
    return result(rule,'violation','Нумерованный пункт начинается со строчной буквы.',dedupe_evidence(ev)[:12],1,'detector','Начать пункт с прописной буквы.') if ev else result(rule,'pass','Высокоуверенные случаи начала нумерованного пункта со строчной буквы не обнаружены.',confidence=1)


def _list_ending(rule,document,numbered=True):
    ev=[]
    for b in narrative_blocks(document):
        if re.search(r'\bАлгоритм\s*:',b.get('text',''),re.I) or formula_like_block(b.get('text','')): continue
        if numbered:
            for item in extract_numbered_items(b['text']):
                if item['body'].rstrip().endswith(';'): ev.append(evidence(b,item['full']))
        else:
            for line in b['text'].splitlines():
                line=line.strip()
                if re.match(r'^(?:[-–—•]|\d+\))\s+',line) and not re.search(r'[.;]$',line): ev.append(evidence(b,line))
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
    pattern=re.compile(r'(?<![\p{L}\p{N}_])(?:[A-ZА-ЯЁ]{2,8}|Recall@\d+|NDCG@\d+|F1@?\d*|κ|τ|γ|≈|≤|≥|\d+[,.]\d+)(?![\p{L}\p{N}_])')
    for item in items:
        if pattern.search(item['text']): ev.append(evidence(item['source'],f"{item['number']}. {item['text']}"))
    return result(rule,'violation','В положениях обнаружены аббревиатуры, метрики или математические обозначения.',dedupe_evidence(ev)[:12],1,'detector','Раскрыть обозначения словами.') if ev else result(rule,'pass','В распознанных положениях аббревиатуры и математические обозначения не обнаружены.',confidence=1)


def _spacing(rule,document):
    initials=[]; percents=[]
    for b in narrative_blocks(document):
        for m in re.finditer(r'(?<![\p{L}\p{N}_])[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]\.(?=[А-ЯЁA-Z][а-яёa-z-])',b['text']): initials.append(evidence(b,contextual(b['text'],m.start(),len(m.group()))))
        for m in re.finditer(r'\d+(?:[,.]\d+)?%(?!\p{L})',b['text']):
            q=contextual(b['text'],m.start(),len(m.group()))
            if not is_likely_table_context(q): percents.append(evidence(b,q))
    if initials: return result(rule,'violation','Обнаружено написание инициалов без пробела перед фамилией.',dedupe_evidence(initials)[:12],1,'detector','Добавить пробел.')
    if percents and document.get('sourceFormat')=='pdf': return result(rule,'uncertain','В PDF знак процента местами прилегает к числу; текстовый слой может терять пробелы.',dedupe_evidence(percents)[:8],0,'detector')
    if percents: return result(rule,'violation','Обнаружено число без пробела перед знаком процента.',dedupe_evidence(percents)[:12],1,'detector','Добавить неразрывный пробел.')
    return result(rule,'pass','Высокоуверенные нарушения пробелов не обнаружены.',confidence=1)


def _dash(rule,document):
    ev=[]
    patterns=[r'(?<=\p{L})\s-\s(?=\p{L})',r'(?<=\p{L})[—–](?=\p{L})',r'(?<=\p{L})\s+[—–](?=\p{L})',r'(?<=\p{L})[—–]\s+(?=\p{L})',r'(?<=\p{L})--(?=\p{L})']
    for b in narrative_blocks(document):
        for p in patterns:
            for m in re.finditer(p,b['text']):
                q=contextual(b['text'],m.start(),len(m.group()))
                if _likely_hyphenated_name(q) or _pdf_line_wrap(b['text'],m.start()): continue
                ev.append(evidence(b,q))
    ev=dedupe_evidence(ev)[:12]
    if not ev: return result(rule,'pass','Высокоуверенные нарушения различия тире и дефиса не обнаружены.',confidence=.98)
    if document.get('sourceFormat')=='pdf': return result(rule,'uncertain','В PDF найдены возможные нарушения тире, но извлечение может терять пробелы.',ev,0,'detector')
    return result(rule,'violation','В DOCX обнаружено тире без требуемых пробелов либо дефис вместо тире.',ev,1,'detector','Использовать среднее тире «–» с пробелами.')


def _generic(rule,document,detector):
    specs=SPECS.get(detector)
    if not specs: return result(rule,'not_checked',f'Детектор {detector} ещё не реализован.')
    ev=[]; fix=None
    for b in _scope_blocks(document,detector,rule):
        for pattern,pattern_fix in specs:
            for m in re.finditer(pattern,b.get('text',''),re.I|re.M):
                # Conservative exclusions for common numeric false positives.
                q=contextual(b['text'],m.start(),len(m.group()))
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
    if detector=='small-numerals': return _small_numerals(rule,document)
    if detector=='defense-punctuation': return _defense(rule,document,True)
    if detector=='defense-symbols': return _defense(rule,document,False)
    if detector=='spacing': return _spacing(rule,document)
    if detector=='dash-spacing': return _dash(rule,document)
    return _generic(rule,document,detector)
