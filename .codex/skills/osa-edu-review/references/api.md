# OSA.Edu API used by this skill

Default base URL: `http://127.0.0.1:8787`.

## Health

- `GET /api/health`

## Formal and semantic review

- `POST /api/jobs` — multipart file upload; the skill sets `developerMode=true` for unattended execution.
- `GET /api/jobs` — locate and monitor the returned job IDs.
- `GET /api/jobs/{id}/report.json`
- `GET /api/jobs/{id}/report.md`
- `GET /api/jobs/{id}/report.pdf`

Terminal states: `completed`, `failed`, `cancelled`. The client also stops at
`awaiting_review`, because unattended structure confirmation could not finish.

## Literature

- `POST /api/literature/jobs` — multipart PDF upload.
- `GET /api/literature/jobs/{id}` — job and embedded result.

Terminal states: `done`, `failed`, `cancelled`.

## Norm control

- `POST /api/normcontrol/jobs` — multipart PDF upload.
- `GET /api/normcontrol/jobs/{id}`
- `GET /api/normcontrol/jobs/{id}/report.pdf`

Terminal states: `completed`, `failed`, `cancelled`.

## Reproducibility

- `GET /api/reproducibility/preflight?repository=...`
- `POST /api/reproducibility/jobs` — multipart PDF plus repository URL.
- `GET /api/reproducibility/jobs/{id}`
- `GET /api/reproducibility/jobs/{id}/result`
- `GET /api/reproducibility/jobs/{id}/log.txt`

Terminal states: `completed`, `failed`, `cancelled`.

The queues are asynchronous. Do not treat successful submission as completed analysis.
