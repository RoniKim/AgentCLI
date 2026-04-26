from __future__ import annotations

import json
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SMOKE_TIMEOUT_SECONDS = 30


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WebConsolePlaywrightSmokeTests(unittest.TestCase):
    @classmethod
    def _asyncio_subprocess_runtime_available(cls) -> bool:
        async def _probe() -> None:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "pass",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

        try:
            asyncio.run(_probe())
            return True
        except (OSError, PermissionError):
            return False

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import expect, sync_playwright
        except Exception as exc:
            raise unittest.SkipTest(
                "Playwright is not installed. Optional setup: "
                f'"{sys.executable}" -m pip install playwright && '
                f'"{sys.executable}" -m playwright install chromium'
            ) from exc

        if not cls._asyncio_subprocess_runtime_available():
            raise unittest.SkipTest(
                "Playwright runtime is unavailable because this environment blocks asyncio subprocess pipes. "
                "Optional setup outside the sandbox: "
                f'"{sys.executable}" -m pip install playwright && '
                f'"{sys.executable}" -m playwright install chromium'
            )

        cls.expect = staticmethod(expect)
        cls.sync_playwright = staticmethod(sync_playwright)

        try:
            from tests.test_web_console_readonly import _write_config, _write_run_bundle
        except Exception as exc:  # pragma: no cover - import failure is environment-specific
            raise RuntimeError(f"Smoke fixture helpers are unavailable: {exc}") from exc

        cls._write_config = staticmethod(_write_config)
        cls._write_run_bundle = staticmethod(_write_run_bundle)

    def setUp(self) -> None:
        self._tmp_root = ROOT / ".test-scratch"
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self._tmp = self._tmp_root / f"{self._testMethodName}_{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.home = self._tmp / "home"
        self.home.mkdir(parents=True, exist_ok=True)

        self._old_home = os.environ.get("AGENTCLI_HOME")
        os.environ["AGENTCLI_HOME"] = str(self.home)
        self.addCleanup(self._restore_home)

        self.config_path = self.home / "configs" / "agentcli.json"
        self.prompts_dir = self.home / "prompts" / "agentcli"
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.run_root = self.repo / ".AgentCLI" / "agent_runs"
        self.latest_run_dir = self.run_root / "20260426-120000"
        self.previous_run_dir = self.run_root / "20260425-120000"
        self.worktree_dir = self._tmp / "worktree"
        self.worktree_dir.mkdir(parents=True, exist_ok=True)

        self._write_fixture_data()
        self.port = _free_port()
        self.server_url = f"http://127.0.0.1:{self.port}"
        self.server_proc: subprocess.Popen[str] | None = None

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self._old_home

    def _write_fixture_data(self) -> None:
        self._write_config(
            self.config_path,
            self.repo,
            prompts_dir="prompts/agentcli",
            goals_completion_level="all",
            iterations=4,
            roles=["PM", "Dev", "QA"],
            telegram={
                "enabled": True,
                "bot_token": "fixture-bot-token",
                "pairing_code": "PAIR-043",
            },
        )

        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0
- [x] Expose read-only progress views
- [ ] Add Playwright browser smoke coverage

## P1
- [ ] Keep Dashboard and Config locale-aware
- [ ] Preserve the worktree review path
""",
        )

        prompt_files = {
            "pm_instructions.md": "# PM Instructions\n\nOpen the dashboard first.\n",
            "dev_instructions.md": "# Dev Instructions\n\nKeep browser coverage deterministic.\n",
            "qa_instructions.md": "# QA Instructions\n\nVerify the console views and the mobile viewport.\n",
            "pm_bootstrap_prompt.md": "# Bootstrap Prompt\n\nProfile: {profile}\nRepo: {repo}\n",
            "pm_incremental_prompt.md": "# Incremental Prompt\n\nKeep the read-only console in sync.\n",
            "dev_task_prompt.md": "# Dev Task Prompt\n\nImplement the browser smoke harness.\n",
            "qa_prompt.md": "# QA Prompt\n\nCheck the web console smoke path.\n",
            "reporter_instructions.md": "# Reporter Instructions\n\nSummarize the smoke result.\n",
            "pm_shutdown_report_prompt.md": "# Shutdown Report Prompt\n\nCapture the run summary.\n",
        }
        for file_name, text in prompt_files.items():
            _write(self.prompts_dir / file_name, text)

        self._write_run_bundle(
            self.previous_run_dir,
            task_id="T42",
            task_title="Locale readiness checkpoint",
            branch="main",
            status="stopped",
            final_rc=0,
            final_reason="stopped",
        )
        self._write_run_bundle(
            self.latest_run_dir,
            task_id="T43",
            task_title="Playwright browser smoke coverage",
            branch="main",
            status="success",
            final_rc=0,
            final_reason="project_complete",
        )

        _write(
            self.latest_run_dir / "logs" / "run.log",
            "2026-04-26 12:00:00 [INFO] cycle started\n2026-04-26 12:02:00 [INFO] smoke fixtures loaded\n",
        )
        _write(
            self.latest_run_dir / "WORKTREE_MERGE_PENDING.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "created_at": "2026-04-26T12:03:00",
                    "source_repo": self.repo.as_posix(),
                    "run_dir": self.latest_run_dir.as_posix(),
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "patch_path": (self.latest_run_dir / "worktree.patch").as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.latest_run_dir / "worktree.patch",
            "\n".join(
                [
                    "diff --git a/web_console/app.js b/web_console/app.js",
                    "--- a/web_console/app.js",
                    "+++ b/web_console/app.js",
                    "@@ -1 +1 @@",
                    "-old",
                    "+new",
                    "",
                    "diff --git a/tests/web_console_playwright_smoke.py b/tests/web_console_playwright_smoke.py",
                    "--- a/tests/web_console_playwright_smoke.py",
                    "+++ b/tests/web_console_playwright_smoke.py",
                    "@@ -1 +1 @@",
                    "-old",
                    "+new",
                    "",
                ]
            ),
        )

    def _start_server(self) -> None:
        assert self.server_proc is None
        command = [
            sys.executable,
            "-m",
            "agent_runner.web",
            "--repo",
            self.repo.as_posix(),
            "--config-path",
            self.config_path.as_posix(),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        env = os.environ.copy()
        env["AGENTCLI_HOME"] = self.home.as_posix()
        env["PYTHONUNBUFFERED"] = "1"
        self.server_proc = subprocess.Popen(  # noqa: S603,S607 - local test server process
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.addCleanup(self._stop_server)
        self._wait_for_server_ready()

    def _stop_server(self) -> None:
        if self.server_proc is None:
            return
        if self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
                self.server_proc.wait(timeout=10)

        if self.server_proc.stdout is not None:
            self.server_proc.stdout.close()
        self.server_proc = None

    def _server_log(self) -> str:
        if self.server_proc is None or self.server_proc.stdout is None:
            return ""
        try:
            return self.server_proc.stdout.read() or ""
        except Exception:
            return ""

    def _wait_for_server_ready(self) -> None:
        deadline = time.monotonic() + SMOKE_TIMEOUT_SECONDS
        last_error: str = ""
        status_url = f"{self.server_url}/api/status"
        while time.monotonic() < deadline:
            if self.server_proc is not None and self.server_proc.poll() is not None:
                raise AssertionError(
                    "agent_runner.web exited before the smoke server became ready.\n"
                    f"Exit code: {self.server_proc.returncode}\n"
                    f"Output:\n{self._server_log()}"
                )
            try:
                with urlopen(status_url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (URLError, OSError, TimeoutError) as exc:
                last_error = str(exc)
                time.sleep(0.25)
        if self.server_proc is not None and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
                self.server_proc.wait(timeout=10)
        raise AssertionError(
            "Timed out waiting for the smoke server to become ready.\n"
            f"Last error: {last_error}\n"
            f"Output:\n{self._server_log()}"
        )

    def _open_page(self, playwright):
        browser = playwright.chromium.launch(headless=True)
        self.addCleanup(browser.close)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1024},
            locale="en-US",
        )
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(self.server_url, wait_until="domcontentloaded")
        return page

    def _open_view(self, page, action: str, view: str, marker: str) -> None:
        page.locator(f'#sidebar [data-action="{action}"]').click()
        self.expect(page.locator("#main")).to_have_attribute("data-view", view)
        self.expect(page.locator("#main")).to_contain_text(marker)

    def test_primary_views_locale_and_mobile_width(self) -> None:
        self._start_server()

        manager = self.sync_playwright()
        try:
            playwright = manager.__enter__()
        except Exception as exc:
            raise unittest.SkipTest(
                "Playwright runtime is unavailable. Optional setup: "
                f'"{sys.executable}" -m pip install playwright && '
                f'"{sys.executable}" -m playwright install chromium'
            ) from exc
        try:
            try:
                page = self._open_page(playwright)
            except Exception as exc:
                raise unittest.SkipTest(
                    "Playwright Chromium is unavailable. Optional setup: "
                    f'"{sys.executable}" -m pip install playwright && '
                    f'"{sys.executable}" -m playwright install chromium'
                ) from exc

            self.expect(page.locator("#main")).to_have_attribute("data-view", "dashboard")
            self.expect(page.locator("#main")).to_contain_text("Pipeline snapshot")

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")
            self.expect(page.locator("#main h2")).to_have_text("Dashboard")
            self.expect(page.locator("#main")).to_contain_text("Current task id")

            page.locator('#topbar [data-action="set-locale-ko"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "ko")
            self.expect(page.locator("#main h2")).not_to_have_text("Dashboard")

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")
            self.expect(page.locator("#main h2")).to_have_text("Dashboard")

            self._open_view(page, "nav-pipeline", "pipeline", "Stage lane")
            self._open_view(page, "nav-logs", "logs", "Live tail")
            self._open_view(page, "nav-backlog", "backlog", "Work queue")
            self._open_view(page, "nav-goals", "goals", "GOALS.md snapshot")

            self._open_view(page, "nav-config", "config", "Field details")
            self.expect(page.locator("#main h2")).to_have_text("Config")
            page.locator('#topbar [data-action="set-locale-ko"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "ko")
            self.expect(page.locator("#main h2")).not_to_have_text("Config")

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")
            self.expect(page.locator("#main h2")).to_have_text("Config")

            self._open_view(page, "nav-prompts", "prompts", "Prompt inventory")
            self.expect(page.locator('[data-prompt-editor-root]')).to_have_attribute("data-prompt-loading", "false")
            self.expect(page.locator("#main")).to_contain_text("FULL READ PREVIEW")

            self._open_view(page, "nav-history", "history", "Run History")
            self._open_view(page, "nav-notifications", "notifications", "Event feed")
            self._open_view(page, "nav-worktree", "worktree", "Pending merge")

            page.set_viewport_size({"width": 390, "height": 844})
            page.locator('#sidebar [data-action="nav-dashboard"]').click()
            self.expect(page.locator("#main")).to_have_attribute("data-view", "dashboard")
            self.expect(page.locator(".topbar__status")).to_be_hidden()

            dimensions = page.evaluate(
                """() => ({
                    innerWidth: window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                })"""
            )
            self.assertLessEqual(dimensions["scrollWidth"], dimensions["innerWidth"])
        finally:
            manager.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
