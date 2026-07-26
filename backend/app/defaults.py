from __future__ import annotations

from .config import CONFIG_DIR


def _read(name: str) -> str:
    path = CONFIG_DIR / name
    return path.read_text(encoding='utf-8').strip() if path.exists() else ''

DEFAULT_PROMPT = _read('semantic-prompt.txt')
DEFAULT_MAP_PROMPT = _read('document-map-prompt.txt')
DEFAULT_ADDITIONAL_CRITERIA = ''
DEFAULT_PROFILE = 'core'

MODELS = [
    {'id':'nvidia/nemotron-3-super-120b-a12b:free','label':'Nemotron 3 Super · бесплатно','provider':'openrouter','tier':'free','contextTokens':1_000_000,'note':'Фиксированная бесплатная модель с контекстом 1 млн токенов.'},
    {'id':'nvidia/nemotron-3-ultra-550b-a55b:free','label':'Nemotron 3 Ultra · бесплатно','provider':'openrouter','tier':'free','contextTokens':1_000_000,'note':'Более крупная бесплатная модель для сложной структуры и смысловой проверки.'},
    {'id':'openrouter/free','label':'OpenRouter Free Router · бесплатно','provider':'openrouter','tier':'free','contextTokens':200_000,'note':'OpenRouter автоматически выбирает доступную бесплатную модель.'},
    {'id':'google/gemma-4-31b-it:free','label':'Gemma 4 31B · бесплатно','provider':'openrouter','tier':'free','contextTokens':262_000,'note':'Быстрый бесплатный вариант для небольших и средних работ.'},
    {'id':'deepseek/deepseek-v4-flash','label':'DeepSeek V4 Flash · production','provider':'openrouter','tier':'production','contextTokens':1_000_000,'note':'Большой контекст и высокая пропускная способность.'},
    {'id':'deepseek/deepseek-v4-pro','label':'DeepSeek V4 Pro · усиленная production','provider':'openrouter','tier':'production','contextTokens':1_000_000,'note':'Усиленный вариант для сложных содержательных правил.'},
    {'id':'google/gemini-2.5-flash','label':'Gemini 2.5 Flash · production','provider':'openrouter','tier':'production','contextTokens':1_000_000,'note':'Экономичный production-вариант.'},
    {'id':'openai/gpt-5.5','label':'GPT-5.5 · premium production','provider':'openrouter','tier':'production','contextTokens':1_000_000,'note':''},
]


def model_definition(model_id: str):
    return next((m for m in MODELS if m['id'] == model_id), None)
