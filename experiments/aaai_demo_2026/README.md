# AAAI 2026 Demo Experiments

This folder contains the AAAI 2026 demo experiments for OSA.Edu reproducibility checking.
The experiments were built from the existing OSA claim extraction and claim-code verification pipeline, with specialized prompts and local scripts for the demo evaluation.

The dataset is a closed thesis/repository dataset and was anonymized as much as possible before preparing the public experiment artifacts.

## Settings

- OSA.Edu experiment runs used `openai/gpt-5.6-luna`.
- LLM-as-judge evaluation used `gpt-5.6-sol`.
- The agent baseline used Codex GPT-5.5 on extra high reasoning.

## Experiments

- Experiment 1, claim extraction: evaluates how well extracted claims cover gold implementation claims using source-span matching, semantic claim matching, and an LLM judge.
- Experiment 2, claim-code verification: evaluates OSA.Edu verification on gold claims against gold implementation statuses using binary classification metrics.
- Experiment 3, end-to-end: evaluates full claim extraction plus repository verification by comparing implementation-rate estimates against gold.

## Main Metrics

- Experiment 1: partial-or-better coverage.
- Experiment 2: F1.
- Experiment 3: MAE.

## Contents

- `scripts/` contains extraction, verification, evaluation, aggregation, and agent-cost helper scripts.
- `prompts/` contains the specialized OSA and agent prompts used for the demo runs.
- `configs/` contains model settings used by the experiment scripts.
- `results/` contains per-thesis JSON outputs and aggregate CSV/Markdown tables.
