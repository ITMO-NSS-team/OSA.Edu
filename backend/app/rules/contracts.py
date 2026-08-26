from __future__ import annotations

"""Compatibility API backed by the canonical rule manifest.

Fact-rule contracts used to live in this Python module. They now live beside each
rule in config/rule-manifest.json so routing, engine selection and fact semantics
cannot drift apart.
"""

from .manifest import load_rule_manifest, manifest_entry


def contract_for(rule_id: str) -> dict | None:
    entry = manifest_entry(rule_id)
    if entry is None or entry.engine.kind.value != 'semantic_fact':
        return None
    engine = entry.engine
    return {
        'kind': engine.kind.value,
        'scope': entry.scope,
        'facts': list(engine.facts),
        'externalKnowledge': engine.externalKnowledge,
        'fixPolicy': engine.fixPolicy,
        'dedupGroup': engine.dedupGroup,
        **({'guidance': engine.guidance} if engine.guidance else {}),
        **({'sharedFactsFrom': engine.sharedFactsFrom} if engine.sharedFactsFrom else {}),
        **({'factNameMap': dict(engine.factNameMap)} if engine.factNameMap else {}),
    }


def fact_items(rule_id: str) -> list[str]:
    value = contract_for(rule_id) or {}
    return list(value.get('facts') or [])


def is_fact_rule(rule_id: str) -> bool:
    return bool(contract_for(rule_id))


# Read-only compatibility snapshot for code that imports the old symbol.
RULE_CONTRACTS = {
    rule_id: contract_for(rule_id)
    for rule_id, entry in load_rule_manifest().rules.items()
    if entry.engine.kind.value == 'semantic_fact'
}
