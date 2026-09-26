from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile
from fastapi.responses import JSONResponse

from backend.app.literature import router as literature_router
from backend.app.literature import store as literature_store


PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def _upload(filename: str = "paper.pdf", content: bytes = PDF_BYTES) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(content))


def _json(response: JSONResponse) -> dict | list:
    return json.loads(response.body.decode("utf-8"))


@contextmanager
def isolated_literature_storage():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with patch.object(literature_store, "LITERATURE_JOBS_FILE", root / "literature_jobs.json"), patch.object(
            literature_router,
            "LITERATURE_UPLOADS_DIR",
            root / "uploads",
        ):
            yield root


class LiteraturePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_literature_job_defaults_to_web_origin(self) -> None:
        with isolated_literature_storage(), patch.object(
            literature_router,
            "model_definition",
            return_value={"id": "test-model", "provider": "gemini"},
        ), patch.object(literature_router, "start_literature_queue"):
            response = await literature_router.create_literature_job_endpoint(
                [_upload()],
                model="test-model",
            )
            payload = _json(response)
            jobs = await literature_store.list_literature_jobs()

        self.assertEqual(201, response.status_code)
        self.assertEqual("web", payload[0]["origin"])
        self.assertEqual("web", jobs[0]["origin"])
        self.assertEqual("queued", jobs[0]["status"])

    async def test_queued_literature_job_accepts_skill_origin(self) -> None:
        with isolated_literature_storage(), patch.object(
            literature_router,
            "model_definition",
            return_value={"id": "test-model", "provider": "gemini"},
        ), patch.object(literature_router, "start_literature_queue"):
            response = await literature_router.create_literature_job_endpoint(
                [_upload()],
                model="test-model",
                origin="skill",
            )
            payload = _json(response)
            jobs = await literature_store.list_literature_jobs()

        self.assertEqual(201, response.status_code)
        self.assertEqual("skill", payload[0]["origin"])
        self.assertEqual("skill", jobs[0]["origin"])

    async def test_one_shot_check_persists_completed_api_history(self) -> None:
        result = {
            "filename": "paper.pdf",
            "reference_count": 0,
            "rows": [],
            "counts": {},
            "model": "test-model",
        }
        with isolated_literature_storage(), patch.object(
            literature_router,
            "model_definition",
            return_value={"id": "test-model", "provider": "gemini"},
        ), patch.object(literature_router, "check_literature", new=AsyncMock(return_value=result)):
            response = await literature_router.check_literature_endpoint(_upload(), model="test-model")
            jobs = await literature_store.list_literature_jobs()
            source_exists = Path(jobs[0]["filePath"]).exists()

        self.assertEqual(result, response)
        self.assertEqual(1, len(jobs))
        self.assertEqual("api", jobs[0]["origin"])
        self.assertEqual("done", jobs[0]["status"])
        self.assertEqual(result, jobs[0]["result"])
        self.assertTrue(source_exists)

    async def test_one_shot_check_persists_failed_api_history(self) -> None:
        with isolated_literature_storage(), patch.object(
            literature_router,
            "model_definition",
            return_value={"id": "test-model", "provider": "gemini"},
        ), patch.object(literature_router, "check_literature", new=AsyncMock(side_effect=RuntimeError("boom"))):
            response = await literature_router.check_literature_endpoint(_upload(), model="test-model")
            jobs = await literature_store.list_literature_jobs()

        self.assertEqual(500, response.status_code)
        self.assertIn("boom", _json(response)["error"])
        self.assertEqual("api", jobs[0]["origin"])
        self.assertEqual("failed", jobs[0]["status"])
        self.assertEqual("boom", jobs[0]["error"])


class LiteratureSkillClientTests(unittest.TestCase):
    def test_skill_literature_submission_marks_origin(self) -> None:
        client = _load_skill_client()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paper = root / "paper.pdf"
            paper.write_bytes(PDF_BYTES)
            args = argparse.Namespace(
                base_url="http://127.0.0.1:8787",
                file=paper,
                model="test-model",
                timeout_seconds=1,
                poll_seconds=0.01,
            )
            submitted: list[dict[str, str]] = []

            def fake_submit(_base_url: str, _path: str, _file_path: Path, fields: dict[str, str], *, file_field: str):
                submitted.append({**fields, "file_field": file_field})
                return [{"id": "job-1"}]

            with patch.object(client, "_submit", side_effect=fake_submit), patch.object(
                client,
                "_poll",
                return_value={"status": "done", "result": {"filename": "paper.pdf"}, "error": None},
            ):
                result = client._run_literature(args, root / "out")

        self.assertEqual("done", result["status"])
        self.assertEqual("skill", submitted[0]["origin"])
        self.assertEqual("test-model", submitted[0]["model"])
        self.assertEqual("files", submitted[0]["file_field"])


def _load_skill_client() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / ".codex" / "skills" / "osa-edu-review" / "scripts" / "osa_edu_client.py"
    spec = importlib.util.spec_from_file_location("osa_edu_client_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load OSA.Edu skill client.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
