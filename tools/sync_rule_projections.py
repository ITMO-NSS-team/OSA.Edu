#!/usr/bin/env python3
"""Regenerate compatibility projections from config/rule-manifest.json.

Runtime code must read the manifest. These projections are retained for older
external tooling and historical tests only; editing them by hand is unsupported.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config'


def main() -> None:
    manifest_path = CONFIG / 'rule-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    rules = manifest.get('rules') or {}

    routing = {
        'version': int(manifest.get('version') or 1),
        'generatedFrom': 'config/rule-manifest.json',
        'rules': {rule_id: dict(entry.get('routing') or {}) for rule_id, entry in rules.items()},
    }
    (CONFIG / 'rule-routing.json').write_text(json.dumps(routing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    abbreviation_rules = {
        rule_id: dict((entry.get('engine') or {}).get('contract') or {})
        for rule_id, entry in rules.items()
        if (entry.get('engine') or {}).get('kind') == 'abbreviation_fact_map'
    }
    abbreviations = {
        'version': int(manifest.get('version') or 1),
        'generatedFrom': 'config/rule-manifest.json',
        'description': 'Compatibility projection only. Runtime contracts live in the canonical rule manifest.',
        'rules': abbreviation_rules,
    }
    (CONFIG / 'abbreviation-rule-contracts.json').write_text(json.dumps(abbreviations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(f'Synced {len(rules)} routing rules and {len(abbreviation_rules)} abbreviation contracts.')


if __name__ == '__main__':
    main()
