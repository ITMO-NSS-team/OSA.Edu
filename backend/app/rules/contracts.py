from __future__ import annotations

"""Pilot rule contracts for the 3.8 fact-first engine.

Only the semantic rules that caused the largest false-positive regressions are
migrated in this release. Other rules keep their existing path. The contract is
intentionally declarative so additional rules can move to the same engine without
adding another bespoke prompt/decision function.
"""

RULE_CONTRACTS: dict[str, dict] = {
    "CORE-2-3": {
        "kind": "semantic_fact",
        "scope": "defense_chapter_matrix",
        "facts": ["analogs", "prototype", "prototype_disadvantages"],
        "externalKnowledge": "forbidden",
        "fixPolicy": "document_only",
        "dedupGroup": "prototype_analysis",
    },
    "CORE-15": {
        "kind": "semantic_fact",
        "scope": "defense_chapter_matrix",
        "facts": ["analogs_inside_chapter", "prototype_inside_chapter", "prototype_disadvantages_inside_chapter"],
        "externalKnowledge": "forbidden",
        "fixPolicy": "document_only",
        "dedupGroup": "prototype_analysis",
    },
    "CORE-8-2": {
        "kind": "semantic_fact",
        "scope": "primary_chapter_conclusions",
        "facts": ["comparison_with_prototype_in_chapter_conclusions"],
        "externalKnowledge": "forbidden",
        "fixPolicy": "document_only",
        "dedupGroup": "prototype_comparison",
    },
}


def contract_for(rule_id: str) -> dict | None:
    return RULE_CONTRACTS.get(str(rule_id))


def fact_items(rule_id: str) -> list[str]:
    value = contract_for(rule_id) or {}
    return list(value.get("facts") or [])


def is_fact_rule(rule_id: str) -> bool:
    return (contract_for(rule_id) or {}).get("kind") == "semantic_fact"
