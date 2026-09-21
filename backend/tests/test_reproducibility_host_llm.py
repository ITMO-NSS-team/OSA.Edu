from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    sys.modules["httpx"] = types.SimpleNamespace(AsyncClient=object)

from backend.app.llm.host_llm import DEFAULT_HOST_LLM_MODEL
from backend.app.reproducibility import diagnostics, runner


class ReproducibilityHostCommandTests(unittest.TestCase):
    def test_host_mode_uses_builtin_wrapper(self) -> None:
        with patch.dict(os.environ, {"REPRODUCIBILITY_USE_HOST_LLM": "true"}, clear=True):
            command = runner.build_osa_command(
                "https://github.com/example/project",
                Path("/tmp/paper.pdf"),
                Path("/tmp/out"),
            )

        self.assertEqual([sys.executable, "-m", "backend.app.reproducibility.osa_host_runner"], command[:3])
        self.assertIn("--paper-analysis", command)
        self.assertIn("--paper", command)

    def test_openrouter_mode_uses_canonical_osa_module(self) -> None:
        with patch.dict(os.environ, {"REPRODUCIBILITY_USE_HOST_LLM": "false"}, clear=True):
            command = runner.build_osa_command(
                "https://github.com/example/project",
                Path("/tmp/paper.pdf"),
                Path("/tmp/out"),
            )

        self.assertEqual([sys.executable, "-m", "osa_tool.run"], command[:3])

    def test_external_command_override_bypasses_builtin_wrapper(self) -> None:
        with patch.dict(
            os.environ,
            {
                "REPRODUCIBILITY_USE_HOST_LLM": "true",
                "REPRODUCIBILITY_OSA_COMMAND": "custom-osa --repo {repository} --paper {paper} --model {model}",
            },
            clear=True,
        ):
            command = runner.build_osa_command(
                "https://github.com/example/project",
                Path("/tmp/paper.pdf"),
                Path("/tmp/out"),
            )

        self.assertEqual("custom-osa", command[0])
        self.assertNotIn("backend.app.reproducibility.osa_host_runner", command)

    def test_host_mode_defaults_to_host_model(self) -> None:
        with patch.dict(os.environ, {"REPRODUCIBILITY_USE_HOST_LLM": "true"}, clear=True):
            self.assertEqual(DEFAULT_HOST_LLM_MODEL, runner.configured_model())

    def test_child_pythonpath_includes_project_root(self) -> None:
        with patch.dict(os.environ, {"PYTHONPATH": "/already-there"}, clear=True):
            env = runner._subprocess_env()

        paths = env["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(str(Path(runner.__file__).resolve().parents[3]), paths[0])
        self.assertIn("/already-there", paths)


class ReproducibilityHostReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_readiness_uses_host_status_without_api_key(self) -> None:
        status = {
            "installed": True,
            "authenticated": True,
            "path": "/tmp/host-bridge",
            "detail": "Host bridge готов.",
            "transport": "host_bridge",
        }
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.app.reproducibility.diagnostics.host_provider_status",
            return_value=status,
        ):
            result = await diagnostics.llm_access(
                "https://openrouter.ai/api/v1",
                DEFAULT_HOST_LLM_MODEL,
                use_host_llm=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("HOST_LLM_BRIDGE_DIR", result["keySource"])
        self.assertEqual("host_bridge", result["transport"])

    async def test_openrouter_readiness_still_requires_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = await diagnostics.llm_access(
                "https://openrouter.ai/api/v1",
                "z-ai/glm-5.3-flash",
            )

        self.assertFalse(result["ok"])
        self.assertEqual("API key не найден.", result["detail"])


if __name__ == "__main__":
    unittest.main()
