from __future__ import annotations

import json
from functools import lru_cache

from ..config import CONFIG_DIR
from ..domain.models import RuleManifestEntryModel, RuleManifestModel


@lru_cache(maxsize=1)
def load_rule_manifest() -> RuleManifestModel:
    path = CONFIG_DIR / 'rule-manifest.json'
    if not path.exists():
        raise RuntimeError(f'Canonical rule manifest is missing: {path}')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return RuleManifestModel.model_validate(payload)
    except Exception as exc:
        raise RuntimeError(f'Invalid canonical rule manifest {path}: {exc}') from exc


def manifest_entry(rule_id: str) -> RuleManifestEntryModel | None:
    return load_rule_manifest().rules.get(str(rule_id))


def runtime_metadata(rule_id: str) -> dict:
    entry = manifest_entry(rule_id)
    if entry is None:
        raise KeyError(f'Rule {rule_id} is absent from config/rule-manifest.json')
    engine = entry.engine
    result = {
        'mode': entry.mode,
        'scope': entry.scope,
        'severity': entry.severity,
        'weight': entry.weight,
        'dedupKey': entry.dedupKey,
        'engineKind': engine.kind.value,
        'routing': entry.routing.model_dump(exclude_none=True),
    }
    if engine.detectorId:
        result['detectorId'] = engine.detectorId
    if engine.candidateFamily:
        result['candidateFamily'] = engine.candidateFamily
    if engine.facts:
        result['facts'] = list(engine.facts)
    if engine.guidance:
        result['ruleGuidance'] = engine.guidance
    if engine.sharedFactsFrom:
        result['sharedFactsFrom'] = engine.sharedFactsFrom
    if engine.factNameMap:
        result['factNameMap'] = dict(engine.factNameMap)
    if engine.globalFactKeys:
        result['globalFactKeys'] = list(engine.globalFactKeys)
    if entry.applicability:
        result['applicability'] = dict(entry.applicability)
    return result
