from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from osa_tool.operations.analysis.artifacts import model_provenance
from osa_tool.utils.response_cleaner import JsonProcessor

DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
SOURCE_FUZZY_THRESHOLD = 90.0
SOURCE_FALLBACK_TOP_K = 3
JUDGE_BATCH_SIZE = 25

SOURCE_RELATION_ORDER = (
    "EXACT",
    "OSA_CONTAINS_GOLD",
    "GOLD_CONTAINS_OSA",
    "SAME_SOURCE_PARTIAL_SPAN",
    "NO_MATCH",
)
SOURCE_RELATIONS = frozenset(SOURCE_RELATION_ORDER)
FOUND_SOURCE_RELATIONS = SOURCE_RELATIONS - {"NO_MATCH"}

CLAIM_RELATION_ORDER = (
    "MATCH",
    "OSA_COVERS_GOLD",
    "GOLD_COVERS_OSA",
    "OVERLAP",
    "NO_MATCH",
)
CLAIM_RELATIONS = frozenset(CLAIM_RELATION_ORDER)
CLAIM_FULL_COVERAGE_RELATIONS = frozenset({"MATCH", "OSA_COVERS_GOLD"})
CLAIM_FRAGMENT_RELATIONS = frozenset({"GOLD_COVERS_OSA", "OVERLAP"})
GOLD_CLAIM_PRIORITY = {
    "MATCH": 4,
    "OSA_COVERS_GOLD": 3,
    "GOLD_COVERS_OSA": 2,
    "OVERLAP": 1,
    "NO_MATCH": 0,
}

SOURCE_JUDGE_SYSTEM_PROMPT = """
You are evaluating whether two source spans refer to the same paper evidence.

You are NOT evaluating whether either span is true and you do NOT have access to the repository.
Compare the GOLD ORIGINAL TEXT and the OSA ORIGINAL TEXT only as source evidence spans.

Assign exactly one relation:
- EXACT: The normalized source spans are the same evidence text.
- OSA_CONTAINS_GOLD: The OSA source span fully contains the gold evidence span and may include neighboring text.
- GOLD_CONTAINS_OSA: The gold source span fully contains the OSA evidence span.
- SAME_SOURCE_PARTIAL_SPAN: The spans overlap or clearly refer to the same local passage, but neither cleanly contains the other.
- NO_MATCH: The spans are from different source passages or are too ambiguous to connect.

Rules:
- Do not use claim meaning alone to connect different source passages.
- Prefer containment labels when one source span is fully included in the other.
- Use SAME_SOURCE_PARTIAL_SPAN only when the evidence spans are visibly from the same local source area.
- Use NO_MATCH for merely similar topics from different passages.

Return ONLY a JSON array. Each item must include exactly:
- pair_id: string copied from input
- relation: one of EXACT, OSA_CONTAINS_GOLD, GOLD_CONTAINS_OSA, SAME_SOURCE_PARTIAL_SPAN, NO_MATCH
- reason: short explanation of the decision
""".strip()

CLAIM_JUDGE_SYSTEM_PROMPT = """
You are evaluating whether two implementation claims express the same independently verifiable proposition.

You are NOT evaluating whether either claim is true and you do NOT have access to the repository.
Compare the GOLD CLAIM and the OSA CLAIM only by semantic meaning.

Assign exactly one relation:
- MATCH: The claims express the same implementation fact. Minor wording, grammar, terminology, or specificity differences are allowed only when they do not change the material proposition.
- OSA_COVERS_GOLD: The OSA claim contains the complete GOLD claim, but adds another material implementation property, component, technology, behavior, condition, or count.
- GOLD_COVERS_OSA: The OSA claim captures only part of the GOLD claim, omitting a material implementation property, component, technology, behavior, condition, or count.
- OVERLAP: The claims share a meaningful implementation fact, but neither one clearly contains the other.
- NO_MATCH: The claims describe different implementation facts, merely concern the same topic, contradict each other, or one is general/background text rather than an implementation claim.

Rules:
- Semantic similarity alone is not sufficient.
- Different technologies are different claims even if they serve the same purpose.
- Different components are different claims.
- Do not treat a general theoretical statement as equivalent to an implementation statement.
- A broader claim must not be MATCH if its added information is independently verifiable.
- Use OSA_COVERS_GOLD when an under-decomposed OSA claim bundles the full GOLD fact with additional facts.
- Use GOLD_COVERS_OSA when the OSA claim is a weaker or less specific fragment of the GOLD fact.
- Minor paraphrasing must not prevent MATCH.
- Do not infer information absent from either claim.

Return ONLY a JSON array. Each item must include exactly:
- pair_id: string copied from input
- relation: one of MATCH, OSA_COVERS_GOLD, GOLD_COVERS_OSA, OVERLAP, NO_MATCH
- reason: short explanation of the decision
""".strip()

ProgressCallback = Callable[[str], None] | None

_SOURCE_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u202f": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)


@dataclass(frozen=True)
class EvaluationClaim:
    claim_id: str
    claim: str
    original_text: str
    source_index: int
    record: dict[str, Any]

    def diagnostic_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "original_text": self.original_text,
            "source_index": self.source_index,
            "section_id": self.record.get("section_id"),
            "section_name": self.record.get("section_name"),
            "section_heading_raw": self.record.get("section_heading_raw"),
            "category": self.record.get("category"),
            "verifiability": self.record.get("verifiability"),
        }


@dataclass(frozen=True)
class LoadedEvaluationClaims:
    claims: list[EvaluationClaim]
    source_format: str
    warnings: list[str]


@dataclass(frozen=True)
class SourceGroup:
    source_id: str
    source_index: int
    original_text: str
    normalized_text: str
    claim_ids: list[str]
    records: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "original_text": self.original_text,
            "normalized_text": self.normalized_text,
            "claim_ids": self.claim_ids,
            "claim_count": len(self.claim_ids),
            "section_ids": _unique_metadata(self.records, "section_id"),
            "section_names": _unique_metadata(self.records, "section_name"),
        }


@dataclass(frozen=True)
class SourceCandidatePair:
    pair_id: str
    gold_source_id: str
    osa_source_id: str
    method: str
    score: float | None = None

    def to_dict(
        self,
        gold_sources: dict[str, SourceGroup],
        osa_sources: dict[str, SourceGroup],
        *,
        relation: str,
        reason: str,
    ) -> dict[str, Any]:
        gold_source = gold_sources[self.gold_source_id]
        osa_source = osa_sources[self.osa_source_id]
        payload: dict[str, Any] = {
            "pair_id": self.pair_id,
            "gold_source_id": self.gold_source_id,
            "osa_source_id": self.osa_source_id,
            "gold_claim_ids": gold_source.claim_ids,
            "osa_claim_ids": osa_source.claim_ids,
            "gold_original_text": gold_source.original_text,
            "osa_original_text": osa_source.original_text,
            "relation": relation,
            "reason": reason,
            "method": self.method,
        }
        if self.score is not None:
            payload["score"] = round(float(self.score), 6)
        return payload


@dataclass(frozen=True)
class SourceComponent:
    component_id: str
    gold_source_ids: list[str]
    osa_source_ids: list[str]
    gold_claim_ids: list[str]
    osa_claim_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "gold_source_ids": self.gold_source_ids,
            "osa_source_ids": self.osa_source_ids,
            "gold_claim_ids": self.gold_claim_ids,
            "osa_claim_ids": self.osa_claim_ids,
        }


@dataclass(frozen=True)
class ClaimCandidatePair:
    pair_id: str
    component_id: str
    gold_claim_id: str
    osa_claim_id: str

    def to_dict(
        self,
        gold_claims: dict[str, EvaluationClaim],
        osa_claims: dict[str, EvaluationClaim],
        *,
        relation: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "component_id": self.component_id,
            "gold_claim_id": self.gold_claim_id,
            "osa_claim_id": self.osa_claim_id,
            "gold_claim": gold_claims[self.gold_claim_id].claim,
            "osa_claim": osa_claims[self.osa_claim_id].claim,
            "relation": relation,
            "reason": reason,
        }


def _progress(callback: ProgressCallback, message: str) -> None:
    if callback:
        callback(message)


def _unique_metadata(records: Sequence[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for record in records:
        value = record.get(field)
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _rate(numerator: int, denominator: int) -> float:
    return round(float(numerator / denominator), 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return (
        round(float(2 * precision * recall / (precision + recall)), 4)
        if precision + recall
        else 0.0
    )


def normalize_source_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_SOURCE_TRANSLATION)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _normalized_text(value: Any, *, field: str, item_label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{item_label} field '{field}' must be a string")
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(
            f"{item_label} field '{field}' must contain non-whitespace text"
        )
    return normalized


def _claim_items(payload: Any, *, path: Path, role: str) -> tuple[list[Any], str]:
    if isinstance(payload, list):
        return payload, "bare"
    if not isinstance(payload, dict):
        raise ValueError(f"{role} claims JSON must be an object or a list: {path}")
    if "claims" in payload:
        items = payload["claims"]
        source_format = "typed"
    elif "result" in payload:
        items = payload["result"]
        source_format = "legacy"
    else:
        raise ValueError(
            f"{role} claims JSON must contain 'claims' or 'result': {path}"
        )
    if not isinstance(items, list):
        raise ValueError(f"{role} claims field must be a list: {path}")
    return items, source_format


def _ensure_unique_ids(claims: Sequence[EvaluationClaim], *, role: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            duplicates.add(claim.claim_id)
        seen.add(claim.claim_id)
    if duplicates:
        sample = ", ".join(sorted(duplicates)[:10])
        raise ValueError(f"{role} claim_id values must be unique; duplicates: {sample}")


def load_gold_claims(path: Path) -> LoadedEvaluationClaims:
    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    items, source_format = _claim_items(payload, path=source_path, role="Gold")
    claims: list[EvaluationClaim] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"Gold claim #{index} must be an object with claim_id, claim, and original_text"
            )
        claim_id = _normalized_text(
            item.get("claim_id"), field="claim_id", item_label=f"Gold claim #{index}"
        )
        claim = _normalized_text(
            item.get("claim"), field="claim", item_label=f"Gold claim #{index}"
        )
        original_text = _normalized_text(
            item.get("original_text"),
            field="original_text",
            item_label=f"Gold claim #{index}",
        )
        claims.append(
            EvaluationClaim(
                claim_id=claim_id,
                claim=claim,
                original_text=original_text,
                source_index=index,
                record=dict(item),
            )
        )
    _ensure_unique_ids(claims, role="Gold")
    return LoadedEvaluationClaims(
        claims=claims, source_format=source_format, warnings=[]
    )


def load_osa_claims(path: Path) -> LoadedEvaluationClaims:
    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    items, source_format = _claim_items(payload, path=source_path, role="OSA")
    claims: list[EvaluationClaim] = []
    generated_ids = 0
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"OSA claim #{index} must be an object with claim and original_text"
            )
        record = dict(item)
        claim = _normalized_text(
            record.get("claim"), field="claim", item_label=f"OSA claim #{index}"
        )
        original_text = _normalized_text(
            record.get("original_text"),
            field="original_text",
            item_label=f"OSA claim #{index}",
        )
        raw_id = record.get("claim_id")
        if isinstance(raw_id, str) and raw_id.strip():
            claim_id = " ".join(raw_id.strip().split())
        else:
            generated_ids += 1
            claim_id = f"osa_{index:04d}"
            record["claim_id"] = claim_id
        claims.append(
            EvaluationClaim(
                claim_id=claim_id,
                claim=claim,
                original_text=original_text,
                source_index=index,
                record=record,
            )
        )
    _ensure_unique_ids(claims, role="OSA")
    warnings = []
    if generated_ids:
        warnings.append(
            f"Generated stable IDs for {generated_ids} OSA claims without claim_id"
        )
    return LoadedEvaluationClaims(
        claims=claims, source_format=source_format, warnings=warnings
    )


def build_source_groups(
    claims: Sequence[EvaluationClaim], *, role: str
) -> list[SourceGroup]:
    prefix = "gold_src" if role == "gold" else "osa_src"
    groups_by_text: dict[str, SourceGroup] = {}
    groups: list[SourceGroup] = []
    for claim in claims:
        normalized = normalize_source_text(claim.original_text)
        group = groups_by_text.get(normalized)
        if group is None:
            group = SourceGroup(
                source_id=f"{prefix}_{len(groups) + 1:03d}",
                source_index=len(groups) + 1,
                original_text=claim.original_text,
                normalized_text=normalized,
                claim_ids=[],
                records=[],
            )
            groups_by_text[normalized] = group
            groups.append(group)
        group.claim_ids.append(claim.claim_id)
        group.records.append(claim.record)
    return groups


def _source_pair_key(gold_source_id: str, osa_source_id: str) -> tuple[str, str]:
    return gold_source_id, osa_source_id


def _next_source_pair_id(pair_number: int) -> str:
    return f"source_pair_{pair_number:06d}"


def _fuzzy_ratio(left: str, right: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.ratio(left, right))
    except ImportError:
        from difflib import SequenceMatcher

        return float(SequenceMatcher(None, left, right).ratio() * 100)


def _heuristic_source_matches(
    gold_sources: Sequence[SourceGroup],
    osa_sources: Sequence[SourceGroup],
    *,
    fuzzy_threshold: float,
) -> tuple[list[dict[str, Any]], set[tuple[str, str]], int]:
    gold_by_id = {source.source_id: source for source in gold_sources}
    osa_by_id = {source.source_id: source for source in osa_sources}
    matches: list[dict[str, Any]] = []
    used_pairs: set[tuple[str, str]] = set()
    pair_number = 1

    def add_pair(
        gold_source: SourceGroup,
        osa_source: SourceGroup,
        *,
        relation: str,
        method: str,
        reason: str,
        score: float | None = None,
    ) -> None:
        nonlocal pair_number
        key = _source_pair_key(gold_source.source_id, osa_source.source_id)
        if key in used_pairs:
            return
        used_pairs.add(key)
        pair = SourceCandidatePair(
            pair_id=_next_source_pair_id(pair_number),
            gold_source_id=gold_source.source_id,
            osa_source_id=osa_source.source_id,
            method=method,
            score=score,
        )
        matches.append(
            pair.to_dict(gold_by_id, osa_by_id, relation=relation, reason=reason)
        )
        pair_number += 1

    for gold_source in gold_sources:
        for osa_source in osa_sources:
            if gold_source.normalized_text == osa_source.normalized_text:
                add_pair(
                    gold_source,
                    osa_source,
                    relation="EXACT",
                    method="normalized_exact",
                    reason="Normalized original_text values are identical.",
                    score=1.0,
                )

    for gold_source in gold_sources:
        for osa_source in osa_sources:
            key = _source_pair_key(gold_source.source_id, osa_source.source_id)
            if key in used_pairs:
                continue
            gold_text = gold_source.normalized_text
            osa_text = osa_source.normalized_text
            if gold_text and gold_text in osa_text:
                add_pair(
                    gold_source,
                    osa_source,
                    relation="OSA_CONTAINS_GOLD",
                    method="normalized_containment",
                    reason="OSA original_text contains the gold source span after normalization.",
                    score=1.0,
                )
            elif osa_text and osa_text in gold_text:
                add_pair(
                    gold_source,
                    osa_source,
                    relation="GOLD_CONTAINS_OSA",
                    method="normalized_containment",
                    reason="Gold original_text contains the OSA source span after normalization.",
                    score=1.0,
                )

    for gold_source in gold_sources:
        for osa_source in osa_sources:
            key = _source_pair_key(gold_source.source_id, osa_source.source_id)
            if key in used_pairs:
                continue
            score = _fuzzy_ratio(
                gold_source.normalized_text, osa_source.normalized_text
            )
            if score >= fuzzy_threshold:
                add_pair(
                    gold_source,
                    osa_source,
                    relation="SAME_SOURCE_PARTIAL_SPAN",
                    method="rapidfuzz_ratio",
                    reason=f"Normalized source spans are textually similar above threshold {fuzzy_threshold}.",
                    score=score,
                )
    return matches, used_pairs, pair_number


def retrieve_source_fallback_pairs(
    gold_sources: Sequence[SourceGroup],
    osa_sources: Sequence[SourceGroup],
    *,
    top_k: int = SOURCE_FALLBACK_TOP_K,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    model: Any | None = None,
    existing_pairs: set[tuple[str, str]] | None = None,
    start_index: int = 1,
    on_progress: ProgressCallback = None,
) -> list[SourceCandidatePair]:
    if top_k < 1:
        raise ValueError("source fallback top_k must be at least 1")
    if not gold_sources or not osa_sources:
        return []
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            'Claim evaluation requires the paper-claims extra. Install it with: pip install "osa_tool[paper-claims]".'
        ) from exc

    if model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                'Claim evaluation requires the paper-claims extra. Install it with: pip install "osa_tool[paper-claims]".'
            ) from exc
        _progress(on_progress, f"Loading source fallback embedding model: {model_name}")
        embedding_model = SentenceTransformer(model_name)
    else:
        embedding_model = model

    _progress(on_progress, f"Embedding {len(gold_sources)} unmatched gold source spans")
    gold_embeddings = np.asarray(
        embedding_model.encode(
            [source.original_text for source in gold_sources],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
    )
    _progress(on_progress, f"Embedding {len(osa_sources)} OSA source spans")
    osa_embeddings = np.asarray(
        embedding_model.encode(
            [source.original_text for source in osa_sources],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
    )
    similarities = np.dot(gold_embeddings, osa_embeddings.T)
    existing_pairs = existing_pairs or set()
    limit = min(top_k, len(osa_sources))
    candidates: list[SourceCandidatePair] = []
    pair_number = start_index
    for gold_index, gold_source in enumerate(gold_sources):
        order = np.argsort(-similarities[gold_index])[:limit]
        for osa_index in order:
            osa_source = osa_sources[int(osa_index)]
            key = _source_pair_key(gold_source.source_id, osa_source.source_id)
            if key in existing_pairs:
                continue
            candidates.append(
                SourceCandidatePair(
                    pair_id=_next_source_pair_id(pair_number),
                    gold_source_id=gold_source.source_id,
                    osa_source_id=osa_source.source_id,
                    method="embedding_llm_fallback",
                    score=float(similarities[gold_index, osa_index]),
                )
            )
            pair_number += 1
    _progress(
        on_progress, f"Retrieved {len(candidates)} source fallback candidate pairs"
    )
    return candidates


def _parse_relation_batch(
    raw: str,
    *,
    expected_pair_ids: set[str],
    allowed_relations: frozenset[str],
    relation_order: Sequence[str],
) -> list[dict[str, str]]:
    parsed = JsonProcessor.parse(raw, expected_type=list)
    pair_ids: list[str] = []
    results: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Each judge result must be an object")
        pair_id = item.get("pair_id")
        relation = item.get("relation")
        reason = item.get("reason", "")
        if not isinstance(pair_id, str) or not pair_id:
            raise ValueError("Each judge result must include a non-empty pair_id")
        if not isinstance(relation, str) or relation not in allowed_relations:
            raise ValueError(
                f"Each judge result must include relation: {', '.join(relation_order)}"
            )
        if not isinstance(reason, str):
            raise ValueError("Each judge result reason must be a string")
        pair_ids.append(pair_id)
        results.append({"pair_id": pair_id, "relation": relation, "reason": reason})

    returned = set(pair_ids)
    if len(pair_ids) != len(returned):
        raise ValueError("Judge response contains duplicate pair_id values")
    if returned != expected_pair_ids:
        missing = sorted(expected_pair_ids - returned)
        unexpected = sorted(returned - expected_pair_ids)
        raise ValueError(
            "Judge response does not cover the requested pair_ids: "
            f"missing={missing[:10]}, unexpected={unexpected[:10]}"
        )
    return results


def parse_source_judge_batch(
    raw: str, *, expected_pair_ids: set[str]
) -> list[dict[str, str]]:
    return _parse_relation_batch(
        raw,
        expected_pair_ids=expected_pair_ids,
        allowed_relations=SOURCE_RELATIONS,
        relation_order=SOURCE_RELATION_ORDER,
    )


def parse_claim_judge_batch(
    raw: str, *, expected_pair_ids: set[str]
) -> list[dict[str, str]]:
    return _parse_relation_batch(
        raw,
        expected_pair_ids=expected_pair_ids,
        allowed_relations=CLAIM_RELATIONS,
        relation_order=CLAIM_RELATION_ORDER,
    )


def judge_source_pairs(
    pairs: Sequence[SourceCandidatePair],
    gold_by_id: dict[str, SourceGroup],
    osa_by_id: dict[str, SourceGroup],
    *,
    handler: Any,
    batch_size: int = JUDGE_BATCH_SIZE,
    on_progress: ProgressCallback = None,
) -> list[dict[str, str]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not pairs:
        return []
    decisions: list[dict[str, str]] = []
    batch_starts = list(range(0, len(pairs), batch_size))
    for batch_number, start in enumerate(batch_starts, start=1):
        batch = list(pairs[start : start + batch_size])
        payload = [
            {
                "pair_id": pair.pair_id,
                "gold_source_id": pair.gold_source_id,
                "gold_original_text": gold_by_id[pair.gold_source_id].original_text,
                "osa_source_id": pair.osa_source_id,
                "osa_original_text": osa_by_id[pair.osa_source_id].original_text,
                "embedding_similarity": round(float(pair.score or 0.0), 6),
            }
            for pair in batch
        ]
        expected_pair_ids = {pair.pair_id for pair in batch}
        prompt = (
            f"## Candidate source pairs\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "## Response contract\n"
            f"Return exactly {len(batch)} objects for pair_ids {sorted(expected_pair_ids)}. "
            "Each object must contain pair_id, relation, and reason. Return the JSON array only."
        )
        _progress(
            on_progress,
            f"Judging source fallback batch {batch_number}/{len(batch_starts)} ({len(batch)} pairs)",
        )
        parsed = handler.send_and_parse(
            prompt,
            partial(parse_source_judge_batch, expected_pair_ids=expected_pair_ids),
            SOURCE_JUDGE_SYSTEM_PROMPT,
        )
        decisions.extend(parsed)
    return decisions


def match_source_groups(
    gold_sources: Sequence[SourceGroup],
    osa_sources: Sequence[SourceGroup],
    *,
    judge_handler: Any,
    embedding_model: Any | None = None,
    embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
    fuzzy_threshold: float = SOURCE_FUZZY_THRESHOLD,
    fallback_top_k: int = SOURCE_FALLBACK_TOP_K,
    judge_batch_size: int = JUDGE_BATCH_SIZE,
    on_progress: ProgressCallback = None,
) -> list[dict[str, Any]]:
    gold_by_id = {source.source_id: source for source in gold_sources}
    osa_by_id = {source.source_id: source for source in osa_sources}
    matches, used_pairs, next_pair_number = _heuristic_source_matches(
        gold_sources,
        osa_sources,
        fuzzy_threshold=fuzzy_threshold,
    )
    found_gold_source_ids = {
        match["gold_source_id"]
        for match in matches
        if match.get("relation") in FOUND_SOURCE_RELATIONS
    }
    missing_gold_sources = [
        source
        for source in gold_sources
        if source.source_id not in found_gold_source_ids
    ]
    _progress(
        on_progress,
        (
            f"Source heuristics produced {len(matches)} source pairs; "
            f"{len(missing_gold_sources)} gold source spans need LLM fallback"
        ),
    )
    fallback_pairs = retrieve_source_fallback_pairs(
        missing_gold_sources,
        osa_sources,
        top_k=fallback_top_k,
        model_name=embedding_model_name,
        model=embedding_model,
        existing_pairs=used_pairs,
        start_index=next_pair_number,
        on_progress=on_progress,
    )
    decisions = judge_source_pairs(
        fallback_pairs,
        gold_by_id,
        osa_by_id,
        handler=judge_handler,
        batch_size=judge_batch_size,
        on_progress=on_progress,
    )
    decisions_by_id = {decision["pair_id"]: decision for decision in decisions}
    for pair in fallback_pairs:
        decision = decisions_by_id[pair.pair_id]
        matches.append(
            pair.to_dict(
                gold_by_id,
                osa_by_id,
                relation=decision["relation"],
                reason=decision.get("reason", ""),
            )
        )
    return matches


def _unique_claim_ids(
    source_ids: Sequence[str], groups_by_id: dict[str, SourceGroup]
) -> list[str]:
    claim_ids: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        for claim_id in groups_by_id[source_id].claim_ids:
            if claim_id not in seen:
                seen.add(claim_id)
                claim_ids.append(claim_id)
    return claim_ids


def build_source_components(
    gold_sources: Sequence[SourceGroup],
    osa_sources: Sequence[SourceGroup],
    source_matches: Sequence[dict[str, Any]],
) -> list[SourceComponent]:
    gold_by_id = {source.source_id: source for source in gold_sources}
    osa_by_id = {source.source_id: source for source in osa_sources}
    gold_order = {source.source_id: index for index, source in enumerate(gold_sources)}
    osa_order = {source.source_id: index for index, source in enumerate(osa_sources)}
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for match in source_matches:
        if match.get("relation") not in FOUND_SOURCE_RELATIONS:
            continue
        gold_node = ("gold", match["gold_source_id"])
        osa_node = ("osa", match["osa_source_id"])
        adjacency[gold_node].add(osa_node)
        adjacency[osa_node].add(gold_node)

    components: list[SourceComponent] = []
    visited: set[tuple[str, str]] = set()
    for start in sorted(adjacency, key=lambda item: (item[0], item[1])):
        if start in visited:
            continue
        queue: deque[tuple[str, str]] = deque([start])
        visited.add(start)
        nodes: set[tuple[str, str]] = set()
        while queue:
            node = queue.popleft()
            nodes.add(node)
            for next_node in adjacency[node]:
                if next_node not in visited:
                    visited.add(next_node)
                    queue.append(next_node)

        gold_source_ids = sorted(
            [source_id for role, source_id in nodes if role == "gold"],
            key=gold_order.__getitem__,
        )
        osa_source_ids = sorted(
            [source_id for role, source_id in nodes if role == "osa"],
            key=osa_order.__getitem__,
        )
        if not gold_source_ids or not osa_source_ids:
            continue
        components.append(
            SourceComponent(
                component_id=f"source_component_{len(components) + 1:03d}",
                gold_source_ids=gold_source_ids,
                osa_source_ids=osa_source_ids,
                gold_claim_ids=_unique_claim_ids(gold_source_ids, gold_by_id),
                osa_claim_ids=_unique_claim_ids(osa_source_ids, osa_by_id),
            )
        )
    return components


def _relation_counts(
    pairs: Sequence[dict[str, Any]], relation_order: Sequence[str]
) -> dict[str, int]:
    counts = {relation: 0 for relation in relation_order}
    for pair in pairs:
        relation = pair.get("relation")
        if relation in counts:
            counts[relation] += 1
    return counts


def _source_metrics(
    *,
    gold_sources: Sequence[SourceGroup],
    osa_sources: Sequence[SourceGroup],
    source_matches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    gold_found = {
        pair["gold_source_id"]
        for pair in source_matches
        if pair.get("relation") in FOUND_SOURCE_RELATIONS
    }
    osa_supported = {
        pair["osa_source_id"]
        for pair in source_matches
        if pair.get("relation") in FOUND_SOURCE_RELATIONS
    }
    gold_total = len(gold_sources)
    osa_total = len(osa_sources)
    precision = _rate(len(osa_supported), osa_total)
    recall = _rate(len(gold_found), gold_total)
    metrics = {
        "source_gold_total": gold_total,
        "source_osa_total": osa_total,
        "source_gold_found": len(gold_found),
        "source_gold_missed": gold_total - len(gold_found),
        "source_osa_supported": len(osa_supported),
        "source_osa_unsupported": osa_total - len(osa_supported),
        "source_precision": precision,
        "source_recall": recall,
        "source_f1": _f1(precision, recall),
    }
    for relation, count in _relation_counts(
        source_matches, SOURCE_RELATION_ORDER
    ).items():
        metrics[f"source_relation_{relation.lower()}"] = count
    return metrics


def _claim_pairs_for_components(
    components: Sequence[SourceComponent],
) -> list[ClaimCandidatePair]:
    pairs: list[ClaimCandidatePair] = []
    pair_number = 1
    for component in components:
        for gold_claim_id in component.gold_claim_ids:
            for osa_claim_id in component.osa_claim_ids:
                pairs.append(
                    ClaimCandidatePair(
                        pair_id=f"claim_pair_{pair_number:06d}",
                        component_id=component.component_id,
                        gold_claim_id=gold_claim_id,
                        osa_claim_id=osa_claim_id,
                    )
                )
                pair_number += 1
    return pairs


def judge_claim_pairs(
    pairs: Sequence[ClaimCandidatePair],
    gold_by_id: dict[str, EvaluationClaim],
    osa_by_id: dict[str, EvaluationClaim],
    *,
    handler: Any,
    batch_size: int = JUDGE_BATCH_SIZE,
    on_progress: ProgressCallback = None,
) -> list[dict[str, str]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not pairs:
        return []
    decisions: list[dict[str, str]] = []
    batch_starts = list(range(0, len(pairs), batch_size))
    for batch_number, start in enumerate(batch_starts, start=1):
        batch = list(pairs[start : start + batch_size])
        payload = [
            {
                "pair_id": pair.pair_id,
                "component_id": pair.component_id,
                "gold_claim_id": pair.gold_claim_id,
                "gold_claim": gold_by_id[pair.gold_claim_id].claim,
                "osa_claim_id": pair.osa_claim_id,
                "osa_claim": osa_by_id[pair.osa_claim_id].claim,
            }
            for pair in batch
        ]
        expected_pair_ids = {pair.pair_id for pair in batch}
        prompt = (
            f"## Candidate claim pairs\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "## Response contract\n"
            f"Return exactly {len(batch)} objects for pair_ids {sorted(expected_pair_ids)}. "
            "Each object must contain pair_id, relation, and reason. Return the JSON array only."
        )
        _progress(
            on_progress,
            f"Judging claim batch {batch_number}/{len(batch_starts)} ({len(batch)} pairs)",
        )
        parsed = handler.send_and_parse(
            prompt,
            partial(parse_claim_judge_batch, expected_pair_ids=expected_pair_ids),
            CLAIM_JUDGE_SYSTEM_PROMPT,
        )
        decisions.extend(parsed)
    return decisions


def _claim_relations_with_decisions(
    pairs: Sequence[ClaimCandidatePair],
    decisions: Sequence[dict[str, str]],
    gold_by_id: dict[str, EvaluationClaim],
    osa_by_id: dict[str, EvaluationClaim],
) -> list[dict[str, Any]]:
    decisions_by_id = {decision["pair_id"]: decision for decision in decisions}
    relations: list[dict[str, Any]] = []
    for pair in pairs:
        decision = decisions_by_id[pair.pair_id]
        relations.append(
            pair.to_dict(
                gold_by_id,
                osa_by_id,
                relation=decision["relation"],
                reason=decision.get("reason", ""),
            )
        )
    return relations


def select_one_to_one_claim_matches(
    claim_relations: Sequence[dict[str, Any]],
    gold_claim_ids: Sequence[str],
    osa_claim_ids: Sequence[str],
) -> list[dict[str, Any]]:
    if not gold_claim_ids or not osa_claim_ids:
        return []
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment
    except ImportError as exc:
        raise RuntimeError(
            'Claim evaluation requires the paper-claims extra. Install it with: pip install "osa_tool[paper-claims]".'
        ) from exc

    gold_index = {claim_id: index for index, claim_id in enumerate(gold_claim_ids)}
    osa_index = {claim_id: index for index, claim_id in enumerate(osa_claim_ids)}
    scores = np.zeros((len(gold_claim_ids), len(osa_claim_ids)), dtype=float)
    pair_by_cell: dict[tuple[int, int], dict[str, Any]] = {}
    for pair in claim_relations:
        if pair.get("relation") != "MATCH":
            continue
        row = gold_index[pair["gold_claim_id"]]
        column = osa_index[pair["osa_claim_id"]]
        scores[row, column] = 1.0
        pair_by_cell[(row, column)] = pair

    if not pair_by_cell:
        return []
    rows, columns = linear_sum_assignment(-scores)
    selected = [
        pair_by_cell[(int(row), int(column))]
        for row, column in zip(rows, columns)
        if scores[int(row), int(column)] > 0
    ]
    return sorted(
        selected,
        key=lambda item: (
            item["component_id"],
            item["gold_claim_id"],
            item["osa_claim_id"],
        ),
    )


def _best_gold_claim_relations(
    gold_claim_ids: Sequence[str],
    claim_relations: Sequence[dict[str, Any]],
) -> dict[str, str]:
    best: dict[str, str] = {claim_id: "NO_MATCH" for claim_id in gold_claim_ids}
    for pair in claim_relations:
        gold_claim_id = pair["gold_claim_id"]
        relation = pair["relation"]
        if gold_claim_id not in best:
            continue
        if GOLD_CLAIM_PRIORITY[relation] > GOLD_CLAIM_PRIORITY[best[gold_claim_id]]:
            best[gold_claim_id] = relation
    return best


def _gold_claim_coverage(
    gold_claim_ids: Sequence[str],
    claim_relations: Sequence[dict[str, Any]],
) -> dict[str, list[str]]:
    best = _best_gold_claim_relations(gold_claim_ids, claim_relations)
    coverage = {
        "exact": [],
        "full_semantic": [],
        "partial": [],
        "overlap_only": [],
        "unrepresented": [],
    }
    for claim_id in gold_claim_ids:
        relation = best.get(claim_id, "NO_MATCH")
        if relation == "MATCH":
            coverage["exact"].append(claim_id)
        elif relation == "OSA_COVERS_GOLD":
            coverage["full_semantic"].append(claim_id)
        elif relation == "GOLD_COVERS_OSA":
            coverage["partial"].append(claim_id)
        elif relation == "OVERLAP":
            coverage["overlap_only"].append(claim_id)
        else:
            coverage["unrepresented"].append(claim_id)
    return coverage


def _atomicity_diagnostics(
    claim_relations: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gold_by_full_osa: dict[str, set[str]] = defaultdict(set)
    osa_fragments_by_gold: dict[str, set[str]] = defaultdict(set)
    for pair in claim_relations:
        relation = pair.get("relation")
        if relation in CLAIM_FULL_COVERAGE_RELATIONS:
            gold_by_full_osa[pair["osa_claim_id"]].add(pair["gold_claim_id"])
        if relation in CLAIM_FRAGMENT_RELATIONS:
            osa_fragments_by_gold[pair["gold_claim_id"]].add(pair["osa_claim_id"])

    under_decomposition = [
        {"osa_claim_id": osa_claim_id, "gold_claim_ids": sorted(gold_claim_ids)}
        for osa_claim_id, gold_claim_ids in sorted(gold_by_full_osa.items())
        if len(gold_claim_ids) > 1
    ]
    over_decomposition = [
        {"gold_claim_id": gold_claim_id, "osa_claim_ids": sorted(osa_claim_ids)}
        for gold_claim_id, osa_claim_ids in sorted(osa_fragments_by_gold.items())
        if len(osa_claim_ids) > 1
    ]
    return under_decomposition, over_decomposition


def _claim_decomposition_metrics(
    *,
    gold_claim_ids: Sequence[str],
    osa_claim_ids: Sequence[str],
    claim_relations: Sequence[dict[str, Any]],
    matches: Sequence[dict[str, Any]],
    gold_coverage: dict[str, list[str]],
    under_decomposition: Sequence[dict[str, Any]],
    over_decomposition: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    exact_match_count = len(matches)
    false_positive = len(osa_claim_ids) - exact_match_count
    false_negative = len(gold_claim_ids) - exact_match_count
    precision = _rate(exact_match_count, len(osa_claim_ids))
    recall = _rate(exact_match_count, len(gold_claim_ids))
    gold_full_semantic_count = len(gold_coverage["exact"]) + len(
        gold_coverage["full_semantic"]
    )
    gold_partial_or_better_count = (
        gold_full_semantic_count
        + len(gold_coverage["partial"])
        + len(gold_coverage["overlap_only"])
    )
    gold_claims_affected_by_under = {
        claim_id for item in under_decomposition for claim_id in item["gold_claim_ids"]
    }
    osa_claims_in_over = {
        claim_id for item in over_decomposition for claim_id in item["osa_claim_ids"]
    }
    metrics = {
        "decomposition_gold_claims_in_found_sources": len(gold_claim_ids),
        "decomposition_osa_claims_in_supported_sources": len(osa_claim_ids),
        "decomposition_exact_match_count": exact_match_count,
        "decomposition_false_positive": false_positive,
        "decomposition_false_negative": false_negative,
        "decomposition_precision": precision,
        "decomposition_recall": recall,
        "decomposition_f1": _f1(precision, recall),
        "coverage_gold_exact_count": len(gold_coverage["exact"]),
        "coverage_gold_exact_rate": _rate(
            len(gold_coverage["exact"]), len(gold_claim_ids)
        ),
        "coverage_gold_full_semantic_count": gold_full_semantic_count,
        "coverage_gold_full_semantic_rate": _rate(
            gold_full_semantic_count, len(gold_claim_ids)
        ),
        "coverage_gold_partial_count": len(gold_coverage["partial"]),
        "coverage_gold_partial_rate": _rate(
            len(gold_coverage["partial"]), len(gold_claim_ids)
        ),
        "coverage_gold_overlap_only_count": len(gold_coverage["overlap_only"]),
        "coverage_gold_overlap_only_rate": _rate(
            len(gold_coverage["overlap_only"]), len(gold_claim_ids)
        ),
        "coverage_gold_unrepresented_count": len(gold_coverage["unrepresented"]),
        "coverage_gold_unrepresented_rate": _rate(
            len(gold_coverage["unrepresented"]), len(gold_claim_ids)
        ),
        "gold_full_semantic_coverage": _rate(
            gold_full_semantic_count, len(gold_claim_ids)
        ),
        "gold_partial_or_better_coverage": _rate(
            gold_partial_or_better_count, len(gold_claim_ids)
        ),
        "under_decomposition_count": len(under_decomposition),
        "gold_claims_affected_by_under_decomposition": len(
            gold_claims_affected_by_under
        ),
        "over_decomposition_count": len(over_decomposition),
        "osa_claims_in_over_decomposition": len(osa_claims_in_over),
    }
    for relation, count in _relation_counts(
        claim_relations, CLAIM_RELATION_ORDER
    ).items():
        metrics[f"claim_relation_{relation.lower()}"] = count
    metrics["true_positive"] = metrics["decomposition_exact_match_count"]
    metrics["false_positive"] = metrics["decomposition_false_positive"]
    metrics["false_negative"] = metrics["decomposition_false_negative"]
    metrics["precision"] = metrics["decomposition_precision"]
    metrics["recall"] = metrics["decomposition_recall"]
    metrics["f1"] = metrics["decomposition_f1"]
    return metrics


def _component_claim_relation_ids(
    components: Sequence[SourceComponent],
    claim_relations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    relation_ids_by_component: dict[str, list[str]] = defaultdict(list)
    for relation in claim_relations:
        relation_ids_by_component[relation["component_id"]].append(relation["pair_id"])
    return [
        {
            **component.to_dict(),
            "claim_relation_ids": relation_ids_by_component.get(
                component.component_id, []
            ),
        }
        for component in components
    ]


def _matched_source_ids(
    source_matches: Sequence[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    gold_source_ids = {
        match["gold_source_id"]
        for match in source_matches
        if match.get("relation") in FOUND_SOURCE_RELATIONS
    }
    osa_source_ids = {
        match["osa_source_id"]
        for match in source_matches
        if match.get("relation") in FOUND_SOURCE_RELATIONS
    }
    return gold_source_ids, osa_source_ids


def _ordered_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def evaluate_claims(
    gold_claims: Sequence[EvaluationClaim],
    osa_claims: Sequence[EvaluationClaim],
    *,
    judge_handler: Any,
    embedding_model: Any | None = None,
    embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
    meta: dict[str, Any] | None = None,
    input_warnings: Iterable[str] = (),
    on_progress: ProgressCallback = None,
    source_fuzzy_threshold: float = SOURCE_FUZZY_THRESHOLD,
    source_fallback_top_k: int = SOURCE_FALLBACK_TOP_K,
    judge_batch_size: int = JUDGE_BATCH_SIZE,
) -> dict[str, Any]:
    gold_by_id = {claim.claim_id: claim for claim in gold_claims}
    osa_by_id = {claim.claim_id: claim for claim in osa_claims}

    _progress(on_progress, "Stage 2/5: grouping original_text source spans")
    gold_sources = build_source_groups(gold_claims, role="gold")
    osa_sources = build_source_groups(osa_claims, role="osa")
    _progress(
        on_progress,
        f"Grouped {len(gold_sources)} gold sources and {len(osa_sources)} OSA sources",
    )

    _progress(on_progress, "Stage 3/5: matching source spans")
    source_matches = match_source_groups(
        gold_sources,
        osa_sources,
        judge_handler=judge_handler,
        embedding_model=embedding_model,
        embedding_model_name=embedding_model_name,
        fuzzy_threshold=source_fuzzy_threshold,
        fallback_top_k=source_fallback_top_k,
        judge_batch_size=judge_batch_size,
        on_progress=on_progress,
    )
    components = build_source_components(gold_sources, osa_sources, source_matches)
    matched_gold_sources, matched_osa_sources = _matched_source_ids(source_matches)
    _progress(
        on_progress,
        f"Built {len(components)} matched source components from {len(source_matches)} source pairs",
    )

    _progress(
        on_progress,
        "Stage 4/5: judging claim decomposition inside matched source components",
    )
    claim_pairs = _claim_pairs_for_components(components)
    claim_decisions = judge_claim_pairs(
        claim_pairs,
        gold_by_id,
        osa_by_id,
        handler=judge_handler,
        batch_size=judge_batch_size,
        on_progress=on_progress,
    )
    claim_relations = _claim_relations_with_decisions(
        claim_pairs, claim_decisions, gold_by_id, osa_by_id
    )
    gold_claim_ids = _ordered_unique(
        claim_id for component in components for claim_id in component.gold_claim_ids
    )
    osa_claim_ids = _ordered_unique(
        claim_id for component in components for claim_id in component.osa_claim_ids
    )
    matches = select_one_to_one_claim_matches(
        claim_relations, gold_claim_ids, osa_claim_ids
    )
    gold_coverage = _gold_claim_coverage(gold_claim_ids, claim_relations)
    under_decomposition, over_decomposition = _atomicity_diagnostics(claim_relations)

    metrics = {
        "gold_total": len(gold_claims),
        "osa_total": len(osa_claims),
        **_source_metrics(
            gold_sources=gold_sources,
            osa_sources=osa_sources,
            source_matches=source_matches,
        ),
        **_claim_decomposition_metrics(
            gold_claim_ids=gold_claim_ids,
            osa_claim_ids=osa_claim_ids,
            claim_relations=claim_relations,
            matches=matches,
            gold_coverage=gold_coverage,
            under_decomposition=under_decomposition,
            over_decomposition=over_decomposition,
        ),
    }
    _progress(
        on_progress,
        (
            "Source metrics: "
            f"precision={metrics['source_precision']}, recall={metrics['source_recall']}, "
            f"f1={metrics['source_f1']}"
        ),
    )
    _progress(
        on_progress,
        (
            "Decomposition metrics: "
            f"exact={metrics['decomposition_exact_match_count']}, "
            f"precision={metrics['decomposition_precision']}, "
            f"recall={metrics['decomposition_recall']}, f1={metrics['decomposition_f1']}"
        ),
    )
    _progress(
        on_progress,
        (
            "Coverage metrics: "
            f"full={metrics['gold_full_semantic_coverage']}, "
            f"partial_or_better={metrics['gold_partial_or_better_coverage']}, "
            f"unrepresented={metrics['coverage_gold_unrepresented_count']}"
        ),
    )

    return {
        "schema_version": "2.0",
        "meta": meta or {},
        "metrics": metrics,
        "source_identification": {
            "gold_sources": [source.to_dict() for source in gold_sources],
            "osa_sources": [source.to_dict() for source in osa_sources],
            "source_matches": source_matches,
            "components": [component.to_dict() for component in components],
            "unmatched_gold_sources": [
                source.to_dict()
                for source in gold_sources
                if source.source_id not in matched_gold_sources
            ],
            "unsupported_osa_sources": [
                source.to_dict()
                for source in osa_sources
                if source.source_id not in matched_osa_sources
            ],
        },
        "claim_decomposition": {
            "components": _component_claim_relation_ids(components, claim_relations),
            "claim_relations": claim_relations,
            "matches": matches,
            "gold_coverage": gold_coverage,
            "under_decomposition": under_decomposition,
            "over_decomposition": over_decomposition,
        },
        "input_warnings": list(input_warnings),
    }


def _matching_settings(model_override: str | None = None) -> Any:
    from osa_tool.config.settings import ConfigManager

    config = ConfigManager()
    settings = config.get_model_settings("default")
    if model_override:
        settings = settings.model_copy(update={"model": model_override})
    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Experiment 1A source identification and claim decomposition."
    )
    parser.add_argument(
        "--gold-claims", required=True, type=Path, help="Gold claims JSON."
    )
    parser.add_argument(
        "--osa-claims", required=True, type=Path, help="OSA claims JSON."
    )
    parser.add_argument("--output", type=Path, default=Path("evaluation_result.json"))
    parser.add_argument(
        "--model",
        default=None,
        help="Optional judge model override for the default OSA LLM settings.",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress stage/progress messages."
    )
    return parser


def _terminal_progress(enabled: bool) -> ProgressCallback:
    if not enabled:
        return None

    def emit(message: str) -> None:
        print(f"[paper-claims:evaluate] {message}", file=sys.stderr, flush=True)

    return emit


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    progress = _terminal_progress(not args.quiet)

    _progress(progress, "Stage 1/5: loading input files")
    gold = load_gold_claims(args.gold_claims)
    osa = load_osa_claims(args.osa_claims)
    _progress(
        progress,
        (
            f"Loaded {len(gold.claims)} gold claims ({gold.source_format}) and "
            f"{len(osa.claims)} OSA claims ({osa.source_format})"
        ),
    )
    for warning in [*gold.warnings, *osa.warnings]:
        _progress(progress, f"Input warning: {warning}")

    from osa_tool.core.llm.llm import ModelHandlerFactory

    _progress(progress, "Preparing claim-matching model from default config")
    settings = _matching_settings(args.model)
    handler = ModelHandlerFactory.build(settings)
    _progress(progress, f"Using claim-matching model: {settings.model}")

    result = evaluate_claims(
        gold.claims,
        osa.claims,
        judge_handler=handler,
        embedding_model_name=args.embedding_model,
        meta={
            "gold_claims_file": str(args.gold_claims),
            "osa_claims_file": str(args.osa_claims),
            "gold_source_format": gold.source_format,
            "osa_source_format": osa.source_format,
            "embedding_model": args.embedding_model,
            "source_fuzzy_threshold": SOURCE_FUZZY_THRESHOLD,
            "source_fallback_top_k": SOURCE_FALLBACK_TOP_K,
            "judge_batch_size": JUDGE_BATCH_SIZE,
            "judge_model": model_provenance(
                handler, configured=settings.model
            ).model_dump(mode="json"),
        },
        input_warnings=[*gold.warnings, *osa.warnings],
        on_progress=progress,
    )

    _progress(progress, f"Stage 5/5: writing evaluation JSON to {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _progress(progress, "Evaluation complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
