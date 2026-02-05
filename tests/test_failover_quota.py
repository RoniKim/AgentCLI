import argparse
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

if "pydantic" not in sys.modules:
    pydantic_stub = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_json_schema(cls):
            return {}

    def Field(default=None, **_kwargs):
        return default

    pydantic_stub.BaseModel = BaseModel
    pydantic_stub.Field = Field
    sys.modules["pydantic"] = pydantic_stub

from agent_runner.cli import DEFAULTS
from agent_runner.cycle import main_async
from agent_runner.preflight import PreflightResult
from agent_runner.runner_entry import _main_async_dispatch
from agent_runner.state import write_backlog_files


@contextmanager
def _fake_agents(dev_exception: Exception | None = None):
    module_backup = dict(sys.modules)

    class Agent:
        def __init__(self, name: str, *args, **kwargs):
            self.name = name

    class Runner:
        @staticmethod
        async def run(agent, prompt, max_turns: int = 0):  # noqa: ARG004 - prompt unused in fake
            if agent.name == "MAUI_Developer" and dev_exception is not None:
                raise dev_exception
            return SimpleNamespace(final_output="ok")

    class ModelSettings:
        def __init__(self, parallel_tool_calls: bool = False):  # noqa: FBT001, FBT002 - fake signature
            self.parallel_tool_calls = parallel_tool_calls

    def set_default_openai_key(_key: str) -> None:
        return None

    class MCPServerStdio:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    agents_mod = types.ModuleType("agents")
    agents_mod.Agent = Agent
    agents_mod.Runner = Runner
    agents_mod.ModelSettings = ModelSettings
    agents_mod.set_default_openai_key = set_default_openai_key

    mcp_mod = types.ModuleType("agents.mcp")
    mcp_mod.MCPServerStdio = MCPServerStdio

    extensions_mod = types.ModuleType("agents.extensions")
    handoff_mod = types.ModuleType("agents.extensions.handoff_prompt")
    handoff_mod.RECOMMENDED_PROMPT_PREFIX = ""

    sys.modules["agents"] = agents_mod
    sys.modules["agents.mcp"] = mcp_mod
    sys.modules["agents.extensions"] = extensions_mod
    sys.modules["agents.extensions.handoff_prompt"] = handoff_mod

    try:
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(module_backup)


def _make_args(repo: Path, run_dir: Path, **overrides) -> argparse.Namespace:
    payload = dict(DEFAULTS)
    payload.update(
        {
            "repo": str(repo),
            "run_dir": str(run_dir),
            "continuous": True,
            "roles": "Dev",
            "iterations": 1,
            "max_turns_per_task": 1,
            "no_build": True,
            "allow_no_diff": True,
            "no_policy_scan": True,
            "run_tests": False,
            "loop": False,
        }
    )
    payload.update(overrides)
    return argparse.Namespace(**payload)


class TestDevQuotaStop(unittest.IsolatedAsyncioTestCase):
    async def test_dev_quota_exception_sets_reason(self) -> None:
        repo = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            write_backlog_files(
                run_dir,
                [
                    {
                        "id": "T1",
                        "title": "Test task",
                        "prompt": "Do nothing",
                        "files": [],
                        "done_when": "No changes required.",
                    }
                ],
            )
            args = _make_args(repo, run_dir)
            os.environ["OPENAI_API_KEY"] = "test"
            with _fake_agents(Exception("You've hit your usage limit")):
                rc = await main_async(args)
            self.assertEqual(rc, 0)
            stop_text = (run_dir / "STOP").read_text(encoding="utf-8", errors="replace")
            self.assertIn("quota", stop_text.lower())
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8", errors="replace"))
            self.assertEqual(summary.get("final", {}).get("reason"), "quota_exhausted")

    async def test_all_tasks_done_breaks_loop(self) -> None:
        repo = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            write_backlog_files(
                run_dir,
                [
                    {
                        "id": "T1",
                        "title": "Complete task",
                        "prompt": "Do nothing",
                        "files": [],
                        "done_when": "No changes required.",
                    }
                ],
            )
            args = _make_args(repo, run_dir, loop=True, loop_sleep_seconds=0, loop_max_cycles=3)
            os.environ["OPENAI_API_KEY"] = "test"
            with _fake_agents():
                rc = await main_async(args)
            self.assertEqual(rc, 0)
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8", errors="replace"))
            self.assertEqual(summary.get("final", {}).get("reason"), "all_tasks_done")
            self.assertEqual(len(summary.get("cycles") or []), 1)


class TestFailoverTrigger(unittest.IsolatedAsyncioTestCase):
    async def test_runner_entry_failover_on_quota(self) -> None:
        repo = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            stop_path = run_dir / "STOP"

            calls: list[str] = []

            class FakeRunner:
                def __init__(self, backend: str, stop: bool):
                    self.backend = backend
                    self.stop = stop

                async def run(self, args, repo_path):  # noqa: ARG002 - signature mirror
                    calls.append(self.backend)
                    if self.stop:
                        stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                    return 0

            def _fake_get_runner(backend: str):
                if backend == "codex":
                    return FakeRunner("codex", True)
                return FakeRunner(backend, False)

            args = argparse.Namespace(**DEFAULTS)
            args.repo = str(repo)
            args.run_dir = str(run_dir)
            args.failover_enabled = True
            args.failover_backends = ["codex", "claudecode"]
            args.failover_on = ["quota_exhausted"]
            args.failover_max_switches = 1

            with mock.patch("agent_runner.runner_entry.get_runner", side_effect=_fake_get_runner), mock.patch(
                "agent_runner.runner_entry.run_preflight",
                return_value=[
                    PreflightResult(backend="codex", ok=True, issues=[]),
                    PreflightResult(backend="claudecode", ok=True, issues=[]),
                ],
            ):
                rc = await _main_async_dispatch(args)

            self.assertEqual(rc, 0)
            self.assertEqual(calls, ["codex", "claudecode"])


if __name__ == "__main__":
    unittest.main()
