from __future__ import annotations

"""Thin rule router.

Semantic document construction and fragment projection live outside this module;
rule behavior comes from config/rule-manifest.json through registry metadata.
"""

from ..document.chapter_linker import _statement_chapter_roles
from ..util import empty_usage
from ..rules.manifest import manifest_entry
from .applicability import evaluate_applicability, technical_dissertation as _core14_applicability
from .fragment_builder import build_fragments, _build_fragments
from .selector_resolver import prerequisite_failure, route_rule, validate_fragments


async def build_routing(*, document: dict, map_value: dict, rules: list[dict]) -> dict:
    fragments = validate_fragments(build_fragments(document, map_value))
    routed: list[dict] = []
    explicit_rules = 0
    fallback_rules = 0

    for rule in rules:
        spec = rule.get('routing') if isinstance(rule.get('routing'), dict) else None
        if spec is None:
            catalog_entry = manifest_entry(str(rule.get('id') or ''))
            spec = catalog_entry.routing.model_dump(exclude_none=True) if catalog_entry else None
        explicit = spec is not None
        if explicit:
            explicit_rules += 1
        else:
            fallback_rules += 1

        prerequisite_reason = prerequisite_failure(spec, fragments)
        if prerequisite_reason:
            spec = {
                'strategy': str((spec or {}).get('onMissingPrerequisite') or 'manual'),
                'reason': prerequisite_reason,
            }

        applicable, applicability_reason = evaluate_applicability(rule, document)
        if applicable is False:
            spec = {
                'strategy': 'manual',
                'reason': applicability_reason or 'Правило неприменимо к этому типу документа.',
            }

        routed.append(route_rule(rule, fragments, spec, explicit=explicit))

    strategy = 'manifest' if fallback_rules == 0 else 'fallback' if explicit_rules == 0 else 'manifest+fallback'
    return {
        'fragments': fragments,
        'routed': routed,
        'strategy': strategy,
        'explicitRules': explicit_rules,
        'fallbackRules': fallback_rules,
        'usage': empty_usage(),
    }
