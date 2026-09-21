---
name: osa-edu-review
description: Run academic-work checks through OSA.Edu, monitor its queues, and save the produced reports. Use for formal/semantic thesis review, literature validation, norm control, or repository-based reproducibility checks; do not use for a generic manual review that is not meant to run OSA.Edu.
---

# OSA.Edu Review

Use OSA.Edu as the checking engine. Do not replace its pipeline with your own assessment or silently change its verdicts.

## Choose the checks

Map the request to one or more client modes:

- `full`: formal and semantic review of PDF or DOCX; this uses unattended structure confirmation.
- `literature`: bibliography/source validation; PDF only.
- `normcontrol`: external norm-control pipeline; PDF only.
- `reproducibility`: compare claims with a repository; PDF plus an explicit repository URL.

For an unspecified general check, use `full`. For a requested complete pipeline, use `full,literature,normcontrol`; add `reproducibility` only when the user supplied a repository. Do not infer or search for a repository merely to broaden the run.

## Run OSA.Edu

1. Locate `scripts/osa_edu_client.py` relative to this skill.
2. Check the server before submitting work:

   ```powershell
   python scripts/osa_edu_client.py health --base-url http://127.0.0.1:8787
   ```

3. For `full` or `literature`, confirm that `configured` is true for the provider of the requested model, or for the first/default model when none was requested. If it is false, report the provider configuration problem; do not silently switch models or pretend that OSA.Edu ran.
   For `reproducibility`, the client runs `/api/reproducibility/preflight`; accept either the OpenRouter/API-backed path or the Host LLM path reported there. Do not require `OPENROUTER_API_KEY` when `REPRODUCIBILITY_USE_HOST_LLM=true` and Host LLM is ready.
4. If a local OSA.Edu checkout is in scope and the server is unavailable, start its backend with the repository's documented command. Otherwise report that the server is required; do not substitute an agent-only review.
5. Submit the requested checks and wait for their terminal states:

   ```powershell
   python scripts/osa_edu_client.py run path/to/work.pdf --checks full,literature --output-dir path/to/results
   ```

   Add `--repository https://host/owner/repository` for reproducibility. Use `--model` or `--profile full` only when requested or needed for the task.
   Add `--exclude-appendices` when the user explicitly asks not to inspect trailing appendices. The client creates a preserved working PDF copy ending before the first real appendix heading found after the dissertation bibliography, records the detected boundary in the manifest, and submits that same copy to every selected check. If the boundary cannot be found reliably, the run stops instead of silently submitting the entire PDF.

The client runs selected modes sequentially, prints progress to stderr, writes an `osa-edu-manifest.json`, and prints that manifest to stdout. A nonzero exit means at least one requested mode failed; keep the saved job payload and expose the actual OSA.Edu error.

## Deliver results

- Return the manifest and produced artifacts without rewriting the underlying findings.
- State which modes were run, skipped, or failed.
- A concise factual summary is fine when requested, but distinguish it from OSA.Edu's own report.
- Do not delete server jobs, uploads, or prior artifacts unless the user explicitly asks.
- Do not automatically retry failed LLM or external-service stages. Retrying can incur cost and must follow the user's request.

Read [references/api.md](references/api.md) only when debugging, extending the client, or calling an endpoint not covered by the script.
