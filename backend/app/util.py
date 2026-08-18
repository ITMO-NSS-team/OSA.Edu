from __future__ import annotations

import copy
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def normalize_text(value: str) -> str:
    value = unicodedata.normalize('NFC', value or '')
    value = value.replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ')
    value = value.replace('\u200b', '').replace('\ufeff', '')
    value = re.sub(r'[ \t]+\n', '\n', value)
    value = re.sub(r'\n{4,}', '\n\n\n', value)
    return value.strip()


def normalized_quote(value: str) -> str:
    value = (value or '').lower().replace('ё', 'е')
    value = value.replace('«', '"').replace('»', '"').replace('“', '"').replace('”', '"')
    return re.sub(r'\s+', ' ', value).strip()


def compact(value: str) -> str:
    return re.sub(r'\s+', ' ', value or '').strip()


def json_clone(value: Any) -> Any:
    return copy.deepcopy(value)


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def empty_usage() -> dict[str, Any]:
    return {'requests':0,'retries':0,'packets':0,'candidates':0,'estimatedInputTokens':0,'rateLimitWaitMs':0,'requestDurationMs':0,'diagnostics':[],'traces':[]}


def merge_usage(target: dict[str, Any], value: dict[str, Any] | None) -> None:
    if not value:
        return
    for key in ('requests','retries','packets','candidates','estimatedInputTokens','rateLimitWaitMs','requestDurationMs'):
        target[key] = int(target.get(key, 0)) + int(value.get(key, 0))
    target.setdefault('diagnostics', []).extend(value.get('diagnostics') or [])
    target.setdefault('traces', []).extend(value.get('traces') or [])


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def map_is_confirmed(map_value: dict | None) -> bool:
    review = (map_value or {}).get('review', {}) if isinstance(map_value, dict) else {}
    return bool(review.get('confirmedByUser') or review.get('autoConfirmed'))
