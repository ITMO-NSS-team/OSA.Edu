from __future__ import annotations

import json
from typing import Any

from ..llm.client import ask_structured_json
from ..util import empty_usage, merge_usage, now_iso
from .map_builder import _parse_structure, _structure_message, refresh_map


def _map_payload(map_value: dict[str, Any]) -> dict[str, Any]:
    """Compact factual map representation for the critic pass."""
    sections = []
    for item in map_value.get("elements") or []:
        if not isinstance(item, dict):
            continue
        sections.append({
            "type": item.get("type"),
            "label": item.get("label"),
            "startBlockId": item.get("startBlockId"),
            "endBlockId": item.get("endBlockId"),
            "anchorBlockIds": list(item.get("blockIds") or [])[:5],
            "quote": item.get("quote"),
            "confidence": item.get("confidence"),
            "state": item.get("state"),
            **({"note": item.get("note")} if item.get("note") else {}),
        })
    relations = []
    for row in map_value.get("relations") or []:
        if not isinstance(row, dict):
            continue
        relations.append({
            "type": row.get("type"),
            "statementIndex": row.get("statementIndex"),
            "chapterStartBlockId": row.get("targetStartBlockId") or row.get("chapterStartBlockId"),
            "confidence": row.get("confidence"),
            "state": row.get("state"),
            "reason": row.get("reason"),
        })
    return {"sections": sections, "relations": relations}


def _signature(map_value: dict[str, Any]) -> tuple:
    sections = tuple(
        (
            str(item.get("type") or ""),
            str(item.get("startBlockId") or ""),
            str(item.get("endBlockId") or ""),
            str(item.get("state") or ""),
        )
        for item in map_value.get("elements") or []
        if isinstance(item, dict)
    )
    relations = tuple(
        (
            str(row.get("type") or ""),
            int(row.get("statementIndex") or 0),
            str(row.get("targetStartBlockId") or row.get("chapterStartBlockId") or ""),
            str(row.get("state") or ""),
        )
        for row in map_value.get("relations") or []
        if isinstance(row, dict)
    )
    return sections, relations


def _verification_issue(message: str) -> dict[str, Any]:
    return {
        "code": "map_verifier_failed",
        "severity": "info",
        "message": message,
        "elementIds": [],
    }


async def verify_document_map(
    document: dict[str, Any],
    map_value: dict[str, Any],
    *,
    provider: str,
    model: str,
    prompt: str,
) -> dict[str, Any]:
    """Second independent LLM pass followed by deterministic revalidation.

    The verifier receives the original blocks and the first-pass map, returns a
    complete proposed map, and never bypasses the existing map parser/validator.
    If the verifier itself fails, the builder result is preserved and marked so
    it cannot be silently treated as a verified cache entry.
    """
    blocks = document.get("blocks") or []
    if not blocks:
        return map_value
    if not prompt.strip():
        return {
            **refresh_map(document, map_value),
            "verification": {
                "status": "failed",
                "corrected": False,
                "reason": "Промпт второго верификатора не настроен.",
                "verifiedAt": now_iso(),
            },
        }

    current_signature = _signature(map_value)
    user_message = (
        _structure_message(blocks)
        + "\n\nCURRENT_MAP:\n"
        + json.dumps(_map_payload(map_value), ensure_ascii=False, separators=(",", ":"))
    )

    combined_usage = empty_usage()
    merge_usage(combined_usage, map_value.get("usage"))
    try:
        response = await ask_structured_json(
            provider=provider,
            model=model,
            system_prompt=prompt,
            user_message=user_message,
            operation="structure",
            packets=1,
            candidates=len(blocks),
        )
        merge_usage(combined_usage, response.get("usage"))
        parsed = _parse_structure(response.get("value"), blocks)

        # An invalid critic answer must never destroy a valid first-pass map.
        if not parsed.get("elements") or parsed.get("warnings"):
            fallback = refresh_map(document, map_value)
            issues = list(fallback.get("issues") or [])
            issues.append(_verification_issue(
                "Второй LLM-проход вернул неполную/невалидную структуру; сохранена карта первого прохода."
            ))
            return {
                **fallback,
                "version": 4,
                "issues": issues,
                "usage": combined_usage,
                "verification": {
                    "status": "failed",
                    "corrected": False,
                    "reason": "; ".join(parsed.get("warnings") or [])[:1200],
                    "verifiedAt": now_iso(),
                },
            }

        candidate = {
            **map_value,
            "version": 4,
            "elements": parsed["elements"],
            "relations": parsed.get("relations") or [],
            "issues": parsed.get("issues") or [],
            "warnings": parsed.get("warnings") or [],
            "usage": combined_usage,
        }
        # Final validator is authoritative for block IDs, ranges and normalized
        # relations. This is deliberately a fresh validation, not old warnings.
        candidate = refresh_map(document, candidate)
        fatal_codes = {"empty_structure", "invalid_boundaries", "invalid_boundary"}
        if any(item.get("code") in fatal_codes for item in candidate.get("issues") or []):
            fallback = refresh_map(document, map_value)
            issues = list(fallback.get("issues") or [])
            issues.append(_verification_issue(
                "Исправленная карта второго LLM-прохода не прошла финальную детерминированную валидацию; сохранён первый проход."
            ))
            return {
                **fallback,
                "version": 4,
                "issues": issues,
                "usage": combined_usage,
                "verification": {
                    "status": "failed",
                    "corrected": False,
                    "reason": "final_validation_failed",
                    "verifiedAt": now_iso(),
                },
            }

        corrected = _signature(candidate) != current_signature
        return {
            **candidate,
            "version": 4,
            "usage": combined_usage,
            "verification": {
                "status": "corrected" if corrected else "confirmed",
                "corrected": corrected,
                "verifiedAt": now_iso(),
            },
        }
    except Exception as exc:
        merge_usage(combined_usage, getattr(exc, "llm_usage", None))
        fallback = refresh_map(document, map_value)
        issues = list(fallback.get("issues") or [])
        issues.append(_verification_issue(
            f"Второй LLM-проход не завершён: {exc}. Сохранена карта первого прохода."
        ))
        return {
            **fallback,
            "version": 4,
            "issues": issues,
            "usage": combined_usage,
            "verification": {
                "status": "failed",
                "corrected": False,
                "reason": str(exc)[:1200],
                "verifiedAt": now_iso(),
            },
        }
