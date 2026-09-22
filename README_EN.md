# OSA.Edu

OSA.Edu is a system for automatic checking of final qualification papers and dissertations.

The system analyzes a document structure, finds key sections of the work, and checks the text against a set of formal and semantic rules. Simple checks use Python code; semantic checks use an LLM through OpenRouter or a subscription-backed Host LLM provider.

A separate "Проверка воспроизводимости" mode compares technical claims from a final qualification paper PDF with the code in a specified repository: it finds supporting evidence in the code, shows the verification result for each claim, confidence, and an explanation. Results can be saved as JSON or rendered as a PDF report in Russian or English.
A separate "Нормоконтроль" mode sends a PDF to the external "Автонормоконтроль" MCP server, tracks execution attempts, and downloads the final PDF report.

A separate "Проверка литературы" mode checks bibliographic references from a PDF: it extracts the source list, classifies source types, searches for DOI, arXiv, URL, and Crossref confirmations, compares metadata, and moves doubtful records into a separate list for manual review.

Experiments for the AAAI 27 Demo Track are located in `experiments/aaai_27_demo_track`: this directory contains scripts, prompts, configs, and aggregated result tables for three evaluation experiments.

## What OSA.Edu Can Do

OSA.Edu can:

- upload works in PDF and DOCX format;
- automatically extract text and document structure;
- find the introduction, goal, tasks, defense statements, chapters, conclusions, final conclusion, and bibliography;
- check the work against formal and semantic rules;
- check abbreviations, formatting, bibliography, defense statement structure, and other elements;
- send a PDF to an external norm-control MCP server and download the resulting PDF report;
- separately check literature and source references: source existence, title, author, year, DOI/arXiv/URL, and other metadata matches;
- maintain a literature-checking queue for multiple PDFs and export the result as TSV, HTML, or PDF;
- show evidence for detected violations with links to the document text;
- mark ambiguous checks for manual review;
- generate user-facing and technical reports.

---

# Quick Start

## Requirements

Before starting, install:

- Python 3.11-3.14
- Node.js 20.19+
- npm
- Git

At least one LLM provider is also required: either an OpenRouter API key or a configured Host LLM provider.

---

## 1. Clone the Repository

```bash
git clone https://github.com/ITMO-NSS-team/OSA.Edu.git
cd OSA.Edu
```

---

## 2. Create a Python Environment

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

## 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

Main backend dependencies:

- FastAPI
- Uvicorn
- PyMuPDF
- httpx
- fastmcp
- python-docx
- ReportLab
- OSA (`osa_tool`)

---

## 4. Install Frontend Dependencies

```bash
npm install
```

---

## 5. Configure an LLM Provider

Create `.env` from the example:

### Windows

```powershell
Copy-Item .env.example .env
```

### Linux / macOS

```bash
cp .env.example .env
```

Use one of the provider options below.

### OpenRouter

Add the API key and keep reproducibility on the API-backed path:

```env
OPENROUTER_API_KEY=your_api_key
REPRODUCIBILITY_USE_HOST_LLM=false
```

### Host LLM Subscription

Use this mode when the backend can call a logged-in Codex-compatible CLI subscription session:

```env
HOST_LLM_COMMAND=codex
REPRODUCIBILITY_USE_HOST_LLM=true
REPRODUCIBILITY_MODEL=gpt-5.6-luna
```

If the backend runs in Docker and the subscription CLI is available on the host, use bridge mode instead:

```env
HOST_LLM_BRIDGE_DIR=/absolute/path/to/osa-edu-host-bridge
REPRODUCIBILITY_USE_HOST_LLM=true
```

Only one of `HOST_LLM_COMMAND` or `HOST_LLM_BRIDGE_DIR` is needed. `REPRODUCIBILITY_OSA_COMMAND` is an advanced override for operators who want to provide their own OSA runner command.

---

## 6. Run

```bash
npm run dev
```

This command starts both:

- FastAPI backend - `http://127.0.0.1:8787`
- React frontend - `http://127.0.0.1:5173`

Open in a browser:

```text
http://127.0.0.1:5173
```

---

## 7. Run in Docker Compose Dev Mode

Docker mode runs the frontend and backend in separate containers:

- FastAPI backend - `http://127.0.0.1:8787`
- React frontend - `http://127.0.0.1:5173`

Dev Compose uses host network mode so the backend can see host VPN routes and host-local links for external norm control. First create `.env` and configure either OpenRouter or Host LLM, as described above. Then run:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Backend runtime data is stored in the named Docker volume `osa-edu-dev_backend_data`, so uploads and `jobs.json` survive container restarts.

For the "Нормоконтроль" tab, the backend must have network access to the MCP endpoint from `.env`; with Docker startup, this is provided by host network mode.

MCP calls in the "Нормоконтроль" tab are limited by a separate deadline per attempt. By default, the backend makes 3 attempts of 30 seconds each for `list_tools`, `submit_document`, and `generate_pdf_report`; downloading the final PDF report uses a separate HTTP timeout of 120 seconds.

---

# How to Use

## Rule-Based Work Checking

After starting the application:

1. Open the checking page.
2. Select a PDF or DOCX file.
3. Select a model.
4. Start processing.
5. Wait for the document structure to be built.
6. Review the detected sections if necessary.
7. Confirm the structure.
8. Wait for rule checking to finish.
9. Review the detected violations.
10. Download the report you need.

The backend supports uploading multiple files in one run.

## Norm Control

For separate norm control through MCP:

1. Open the "Нормоконтроль" tab.
2. If necessary, click "Проверить MCP" to make sure the endpoint is available.
3. Upload one PDF file.
4. Start norm control and wait for the external DAG to finish.
5. If the check fails, open the MCP log with attempts and messages from the external server.
6. Download the finished norm-control PDF report.

## Literature and Source Checking

For separate bibliography checking:

1. Open the "Проверка литературы" tab.
2. Select a production model/provider.
3. Upload one or more PDF files.
4. Add the files to the queue and wait for checking to finish.
5. Review sources in the "Требуют внимания", "Подтверждено", and "Все" tabs.
6. Open the found evidence links and download TSV, HTML, or PDF if you need an export.

Literature checking is conservative: if no reliable candidate is found, the record is marked as `UNVERIFIED`, not as proven source fabrication. The `LIKELY_HALLUCINATED` status is assigned only when confidence is elevated and independent web evidence is available.

## Reproducibility Checking

1. Open the "Проверка воспроизводимости" tab.
2. Enter the project Git repository URL.
3. Upload the final qualification paper PDF.
4. Start the check.
5. Wait for technical claims to be extracted and checked against the code.
6. Review statistics, each claim result, confidence, and found evidence.
7. Download JSON or a PDF report if necessary.

Reproducibility keeps claim extraction and repository verification inside OSA's canonical `--paper-analysis` pipeline. OSA.Edu manages upload, queueing, progress, presentation, and provider wiring. When `REPRODUCIBILITY_USE_HOST_LLM=true`, OSA.Edu runs OSA through a wrapper that patches OSA model calls to the Host LLM provider, so `/api/reproducibility/status`, `/api/reproducibility/preflight`, and job creation do not require `OPENROUTER_API_KEY` if `HOST_LLM_COMMAND` or `HOST_LLM_BRIDGE_DIR` is ready.

## Using OSA.Edu as a Codex Skill

The repository includes `.codex/skills/osa-edu-review`. In Codex, ask to use `$osa-edu-review` after the OSA.Edu backend is running.

Check the server:

```bash
python .codex/skills/osa-edu-review/scripts/osa_edu_client.py health --base-url http://127.0.0.1:8787
```

Run formal and literature checks:

```bash
python .codex/skills/osa-edu-review/scripts/osa_edu_client.py run path/to/work.pdf --checks full,literature --output-dir osa-edu-results
```

Run repository reproducibility:

```bash
python .codex/skills/osa-edu-review/scripts/osa_edu_client.py run path/to/work.pdf --checks reproducibility --repository https://github.com/owner/repository --output-dir osa-edu-results
```

The client saves `osa-edu-manifest.json` plus the original job payloads and reports under the output directory. It returns OSA.Edu's findings as produced by the server.

---

# How Checking Works

General pipeline:

```text
PDF / DOCX
    ->
Text and block extraction
    ->
Document structure construction
    ->
Structure checking and recovery
    ->
Document Map
    ->
Document fact collection
    ->
Rule candidate search
    ->
Python / LLM checks
    ->
Evidence verification
    ->
Rule results
    ->
Report
```

## 1. Document Extraction

PDF processing uses PyMuPDF.

The document is converted into a set of blocks with information about:

- page;
- text;
- coordinates;
- block type;
- position inside the document.

This makes it possible to avoid treating a PDF as one large text string.

---

## 2. Document Map

After extraction, a semantic document map is built.

The system tries to identify:

- work title;
- introduction;
- goal;
- tasks;
- defense statements;
- chapters;
- chapter conclusions;
- final conclusion;
- bibliography;
- other significant document parts.

The system relies not only on exact heading names, but also on their meaning.

For example:

```text
Цель работы
Research Goal
Objective
```

can be recognized as the same section type: `goal`.

---

## 3. Structure Checking

After the initial map is built, it is checked additionally.

If an important section was not found, the system can try to recover it using:

- text markers;
- block meaning;
- position inside the document;
- neighboring sections.

Before deep checking, the structure can be reviewed and corrected by the user.

---

## 4. Rule Checking

Rules are checked in several ways.

### Python Detectors

Used when a rule can be checked unambiguously.

For example:

- heading formatting;
- periods and dashes;
- forbidden words;
- numbering;
- list format;
- individual bibliography requirements.

### Candidate + LLM

Python first searches for potentially suspicious fragments.

The LLM receives not the whole document, but only the found candidates, and decides whether a specific case is a violation.

### Semantic Checks

For semantic rules, the relevant document section is analyzed.

For example:

- work goal;
- defense statements;
- analogues and prototypes;
- compliance of the scientific result with rule requirements.

---

## 5. Evidence

Every confirmed violation must be linked to a fragment of the source document.

As a result, the user receives not only a message:

```text
Rule violated
```

but also:

```text
where it was found
->
page
->
text fragment
->
explanation
->
correction recommendation
```

---

## 6. Norm Control

The norm-control subsystem runs separately from the main rule pipeline and does not use Document Map.

It performs the following steps:

```text
PDF
    ->
Local file save
    ->
MCP submit_document
    ->
External norm-control DAG
    ->
MCP generate_pdf_report
    ->
PDF report download
    ->
Attempt log, errors, warnings, and finished report
```

The backend stores a local job queue, records MCP endpoint and DAG parameters, shows attempts for `list_tools`, `submit_document`, and `generate_pdf_report`, and saves the downloaded PDF report after successful completion.

---

## 7. Literature and Source Checking

The literature-checking subsystem runs separately from the main rule pipeline.

It performs the following steps:

```text
PDF
    ->
Bibliography search and extraction
    ->
Individual bibliographic record normalization
    ->
Source type classification
    ->
DOI / arXiv / direct URL / Crossref precheck
    ->
Deterministic metadata comparison
    ->
LLM comparison of ambiguous candidates
    ->
Deep web checking of doubtful sources
    ->
Statuses, evidence links, and TSV
```

For each record, the system tries to determine the source type: article, preprint, book, standard, report, dataset, documentation, repository, web source, or another source. Strong matches by identifiers and metadata are resolved deterministically, while the LLM is used for ambiguous cases and web checking.

The result contains the source record, found bibliographic record, evidence URL, status, and comment. Main statuses:

- `OK` - source confirmed;
- `OK_MINOR_MISMATCH` - source found, but there are small discrepancies;
- `METADATA_MISMATCH` - the found source conflicts with the record in the work;
- `SUSPICIOUS` - record requires manual checking;
- `LIKELY_HALLUCINATED` - the source is very likely not confirmed;
- `UNVERIFIED` - no reliable confirmation found;
- `ERROR` - technical checking error.

---

# Reports

After checking finishes, several formats are available.

### User PDF

A short report for the work author.

Contains:

- main violations;
- understandable explanation;
- evidence;
- correction recommendations.

### Developer PDF

An extended technical report.

Additionally contains:

- rule-checking method;
- coverage;
- technical statuses;
- evidence;
- LLM diagnostics;
- document structure information.

### JSON

Full structured check result.

Useful for:

- integrations;
- automatic processing;
- testing;
- comparing results from different checker versions.

### Markdown

Text version of the technical protocol.

### Literature-Checking TSV

On the "Проверка литературы" page, the result can be downloaded as TSV, HTML, or printed to PDF. The TSV contains source number, source type, verdict/status, source record, found record, evidence URL, and comment.

### Norm-Control PDF Report

On the "Нормоконтроль" page, after a successful external run, a downloadable PDF report generated by the "Автонормоконтроль" MCP server is available.

---

# Project Structure

```text
OSA.Edu/
|
|-- backend/
|   `-- app/
|       |-- checking/        # check execution
|       |-- document/        # Document Map and document structure
|       |-- domain/          # data models
|       |-- literature/      # separate literature queue and checking
|       |-- llm/             # LLM integration
|       |-- normcontrol/     # norm-control queue and MCP integration
|       |-- reproducibility/ # OSA integration and reproducibility checking
|       |-- orchestration/   # orchestration pipeline
|       |-- routing/         # rule-checking method selection
|       |-- rules/           # rule loading and processing
|       |
|       |-- main.py          # FastAPI
|       |-- queue.py         # checking queue
|       |-- extraction.py    # extracted document handling
|       |-- pdf_blocks.py    # PDF blocks
|       |-- reporting.py     # Markdown report
|       |-- pdf_reporting.py # developer PDF
|       `-- user_pdf_reporting.py
|
|-- config/
|   |-- rule-manifest.json
|   |-- rule-routing.json
|   |-- abbreviation-rule-contracts.json
|   |-- candidate-families.json
|   |-- candidate-prompt.txt
|   |-- semantic-prompt.txt
|   `-- document-map-prompt.txt
|
|-- experiments/
|   `-- aaai_27_demo_track/  # AAAI 27 Demo Track experiments
|
|-- rules-data/
|   `-- source rule data
|
|-- src/
|   |-- components/
|   |   |-- CheckPage.tsx
|   |   |-- LiteraturePage.tsx
|   |   |-- NormControlPage.tsx
|   |   |-- ReproducibilityPage.tsx
|   |   |-- PromptPage.tsx
|   |   |-- ReportsPage.tsx
|   |   `-- RulesPage.tsx
|   |
|   `-- App.tsx
|
|-- tools/
|   `-- sync_rule_projections.py
|
|-- .env.example
|-- requirements.txt
|-- package.json
|-- package-lock.json
`-- vite.config.ts
```

---

# Where Rules Are Stored

Main rule registry:

```text
config/rule-manifest.json
```

It describes rules and is used as the primary source of their configuration.

Related projections:

```text
config/rule-routing.json
config/abbreviation-rule-contracts.json
```

They are synchronized with:

```text
tools/sync_rule_projections.py
```

When changing the manifest, it is important to make sure derived configurations do not drift from it.

---

# Backend API

Main FastAPI file:

```text
backend/app/main.py
```

Main endpoints:

```text
GET  /api/health
GET  /api/rules

GET  /api/jobs
POST /api/jobs

GET  /api/jobs/{id}/structure
POST /api/jobs/{id}/confirm-structure

POST /api/jobs/{id}/retry
POST /api/jobs/{id}/restart
POST /api/jobs/{id}/retry-failed

GET /api/jobs/{id}/report.pdf
GET /api/jobs/{id}/developer-report.pdf
GET /api/jobs/{id}/report.json
GET /api/jobs/{id}/report.md

GET    /api/normcontrol/jobs
GET    /api/normcontrol/status
GET    /api/normcontrol/jobs/{id}
POST   /api/normcontrol/jobs
POST   /api/normcontrol/jobs/{id}/retry
DELETE /api/normcontrol/jobs/{id}
GET    /api/normcontrol/jobs/{id}/report.pdf

GET    /api/literature/jobs
GET    /api/literature/jobs/{id}
POST   /api/literature/jobs
POST   /api/literature/jobs/{id}/cancel
POST   /api/literature/jobs/{id}/retry
DELETE /api/literature/jobs/{id}

POST /api/literature/check

GET    /api/reproducibility/status
GET    /api/reproducibility/preflight
GET    /api/reproducibility/jobs
POST   /api/reproducibility/jobs
GET    /api/reproducibility/jobs/{id}
GET    /api/reproducibility/jobs/{id}/result
GET    /api/reproducibility/jobs/{id}/log
POST   /api/reproducibility/jobs/{id}/cancel
POST   /api/reproducibility/jobs/{id}/retry
POST   /api/reproducibility/jobs/{id}/resume
DELETE /api/reproducibility/jobs/{id}
```

---

# Runtime Data

Runtime data is created locally in:

```text
data/
```

When running through `docker-compose.dev.yml`, this path is inside a named Docker volume.

The following paths are used in particular:

```text
data/uploads/
data/uploads/literature/
data/extracted/
data/jobs.json
data/normcontrol/uploads/
data/normcontrol/reports/
data/normcontrol/jobs.json
data/literature_jobs.json
data/reproducibility/uploads/
data/reproducibility/runs/
data/reproducibility/jobs.json
```

---

# Settings

Main settings are passed through `.env`.

The minimum required LLM setting is one of these provider configurations:

```env
OPENROUTER_API_KEY=

# or
HOST_LLM_COMMAND=codex
# or
HOST_LLM_BRIDGE_DIR=/absolute/path/to/bridge

```

`.env.example` also contains parameters for:

- number of parallel requests;
- rate limits;
- retry counts;
- LLM batch sizes;
- Host LLM subscription transport: `HOST_LLM_COMMAND`, `HOST_LLM_BRIDGE_DIR`, `HOST_LLM_REQUEST_TIMEOUT_SECONDS`;
- candidate recovery;
- abbreviation processing;
- MCP endpoint, DAG, attempt count, and timeouts for the "Нормоконтроль" tab: `NORMCONTROL_MCP_URL`, `NORMCONTROL_DAG_ID`, `NORMCONTROL_MCP_ATTEMPTS`, `NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS`, `NORMCONTROL_HTTP_TIMEOUT_SECONDS`;
- literature-checking model and web stage: `LITERATURE_REVIEW_MODEL`, `LITERATURE_WEB_SEARCH_ENABLED`, `LITERATURE_WEB_MAX_ATTEMPTS`, `LITERATURE_WEB_FALLBACK_TO_PLUGIN`, `LITERATURE_WEB_SEARCH_ENGINE`, `LITERATURE_WEB_PROVIDER_ORDER`;
- optional contact for the Crossref polite pool: `CROSSREF_MAILTO`.
- reproducibility-checking parameters: OSA model, Host LLM routing, timeout, context window, max tokens, and retry count (`REPRODUCIBILITY_MODEL`, `REPRODUCIBILITY_USE_HOST_LLM`, `REPRODUCIBILITY_TIMEOUT_SECONDS`, `REPRODUCIBILITY_CONTEXT_WINDOW`, `REPRODUCIBILITY_MAX_TOKENS`, `REPRODUCIBILITY_LLM_MAX_RETRIES`);

Usually, the default values do not need to be changed.

---

# Authorship

Initial version of the literature/references checking subsystem was authored by Ivan Molodetskikh (MSU).

---

# What to Do If Checking Fails

If individual LLM checks did not finish, only those checks can be retried:

```text
Retry failed
```

If the entire rule check needs to be rerun against the already prepared structure:

```text
Retry
```

If Document Map needs to be built again:

```text
Restart
```

---

# Limitations

OSA.Edu is an automatic norm-control assistant.

The check result:

- may contain false positives;
- may miss individual violations;
- semantic checks may require manual confirmation;
- is not an official decision on whether the work may be defended.

Special attention should be paid to rules marked by the system as ambiguous or technically incomplete.

---

# Reproducibility Checking

The separate "Проверка воспроизводимости" tab lets the user check how well claims from a final qualification paper are supported by the project code.

To start checking, the user provides:

- project repository link;
- final qualification paper PDF file.

After that, OSA.Edu analyzes the work text, extracts checkable technical claims, and compares them with repository contents.

The repository analysis itself remains in OSA. If `REPRODUCIBILITY_USE_HOST_LLM=true`, OSA.Edu supplies a Host LLM adapter for OSA model calls; otherwise OSA uses the configured OpenRouter/API-compatible key. The optional `REPRODUCIBILITY_OSA_COMMAND` override bypasses the built-in wrapper and is intended for operators who provide their own runner.

For example, if the work claims that a specific algorithm is implemented, a specific model is used, or a failover mechanism is provided, the system tries to find confirmation in the project code.

For each claim, the user receives:

claim text from the final qualification paper;
verification result;
confidence level;
section of the work from which it was extracted;
file or other repository fragment supporting the result;
short explanation.

The page also shows overall statistics: how many claims were extracted, how many were checked, how many were confirmed, and how many were excluded from checking.

The analysis result can be saved as JSON, and a PDF report can also be generated in Russian or English.
