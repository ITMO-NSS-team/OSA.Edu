"""Optional Hunspell hints, never an automatic verdict about a scientific term."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import DATA_DIR


@lru_cache(maxsize=8)
def _load(prefix: str):
    from spylls.hunspell import Dictionary
    from spylls.hunspell import readers
    from spylls.hunspell.readers.file_reader import FileReader

    class ClosingReader(FileReader):
        def reset_encoding(self, encoding):
            previous = self.io
            try:
                super().reset_encoding(encoding)
            finally:
                previous.close()

    # Spylls 0.1.7 leaves file readers open, including when SET changes their
    # encoding. Keep ownership here instead of leaking handles on configuration changes.
    aff_reader = ClosingReader(prefix + '.aff')
    try:
        aff, context = readers.read_aff(aff_reader)
    finally:
        aff_reader.io.close()
    dic_reader = ClosingReader(prefix + '.dic', encoding=context.encoding)
    try:
        dic = readers.read_dic(dic_reader, aff=aff, context=context)
    finally:
        dic_reader.io.close()
    return Dictionary(aff, dic)


def dictionary():
    import spylls.hunspell
    configured = os.getenv('LANGUAGE_DICTIONARY_PATH', '').strip()
    candidates = [configured] if configured else [
        str(DATA_DIR / 'dictionaries' / 'ru_RU'),
        '/usr/share/hunspell/ru_RU', '/usr/share/myspell/dicts/ru_RU',
        '/opt/homebrew/share/hunspell/ru_RU', '/usr/local/share/hunspell/ru_RU',
        str(Path(spylls.hunspell.__file__).parent / 'data' / 'ru' / 'ru'),
    ]
    for value in candidates:
        if value and Path(value + '.aff').is_file() and Path(value + '.dic').is_file():
            return _load(value)
    return None


def allowed_terms() -> set[str]:
    path = os.getenv('LANGUAGE_ALLOWED_TERMS_FILE', '').strip()
    if not path:
        return set()
    return {line.strip().casefold() for line in Path(path).read_text('utf-8').splitlines()
            if line.strip() and not line.lstrip().startswith('#')}


def candidates(fragments: list[dict[str, Any]], lexicon=None) -> list[dict[str, Any]]:
    """Recall hints include unknown Russian words; Latin/mixed names are excluded.

    Suggestions are deliberately not generated: unknown != misspelled, and a
    dictionary cannot establish the correct word in its sentence.
    """
    lexicon = lexicon if lexicon is not None else dictionary()
    if lexicon is None:
        return []
    allowed = allowed_terms()
    result = []
    for fragment in fragments:
        for match in re.finditer(r'(?<![\w-])[А-Яа-яЁё]{4,}(?![\w-])', fragment['text']):
            word = match.group()
            if word.isupper() or word.casefold() in allowed or lexicon.lookup(word):
                continue
            result.append({'fragment_id': fragment['id'], 'word': word, 'start': match.start()})
    return result

