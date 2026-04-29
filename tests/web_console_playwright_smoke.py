from __future__ import annotations

from collections import deque
import json
import asyncio
import os
import re
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
    DESKTOP_PRIMARY_ROUTES = (
        "dashboard",
        "pipeline",
        "logs",
        "backlog",
        "goals",
        "config",
        "prompts",
        "history",
        "notifications",
        "worktree",
        "landing",
        "mobile",
    )

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
            from tests.test_web_console_readonly import (
                _make_web_console_live_run_fixtures,
                _write_config,
                _write_run_bundle,
            )
        except Exception as exc:  # pragma: no cover - import failure is environment-specific
            raise RuntimeError(f"Smoke fixture helpers are unavailable: {exc}") from exc

        cls._make_web_console_live_run_fixtures = staticmethod(_make_web_console_live_run_fixtures)
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

    def _open_page(self, playwright, before_goto=None):
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1024},
            locale="en-US",
        )
        page = context.new_page()
        if before_goto is not None:
            before_goto(context, page)
        page.goto(self.server_url, wait_until="domcontentloaded")
        return page

    def _close_playwright(self, manager) -> None:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            if "Event loop is closed" not in str(exc):
                raise

    def _open_view(self, page, action: str, view: str, marker: str) -> None:
        page.locator(f'#sidebar [data-action="{action}"], #sidebar [data-nav="{view}"]').first.click()
        self.expect(page.locator("#main")).to_have_attribute("data-view", view)
        self.expect(page.locator("#main")).to_contain_text(re.compile(re.escape(marker), re.IGNORECASE))

    def _assert_desktop_route_layout(self, page, route: str) -> None:
        metrics = page.evaluate(
            """(route) => {
                const visible = (el) => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };
                const heights = (selector) => Array.from(document.querySelectorAll(selector))
                    .filter(visible)
                    .map((el) => Math.round(el.getBoundingClientRect().height * 10) / 10);
                const root = document.documentElement;
                const body = document.body;
                const main = document.querySelector('#main');
                const overflowOffenders = Array.from(document.querySelectorAll('#main *'))
                    .filter((el) => {
                        if (!visible(el)) return false;
                        const style = getComputedStyle(el);
                        const allowedOverflow = ['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX);
                        const formControl = ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName);
                        const scrollRegion = Boolean(el.closest('.log-feed__scroll, .scroll-box, .prompt-preview__text'));
                        return el.scrollWidth - el.clientWidth > 2 && !allowedOverflow && !formControl && !scrollRegion;
                    })
                    .slice(0, 8)
                    .map((el) => ({
                        tag: el.tagName.toLowerCase(),
                        className: String(el.className || ''),
                        text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 160),
                        scrollWidth: el.scrollWidth,
                        clientWidth: el.clientWidth,
                    }));
                return {
                    route,
                    innerWidth: window.innerWidth,
                    rootClientWidth: root.clientWidth,
                    documentScrollWidth: root.scrollWidth,
                    bodyScrollWidth: body.scrollWidth,
                    mainClientWidth: main ? main.clientWidth : 0,
                    mainScrollWidth: main ? main.scrollWidth : 0,
                    overflowOffenders,
                    buttonHeights: heights('#topbar .button:not(.button--tiny), #main .button:not(.button--tiny)'),
                    filterHeights: heights('#main .filter-chip, #main .control-chip, #main .modal-tab'),
                    fieldHeights: heights('#main input.field-control, #main select.field-control, #main .log-tail-input'),
                    navHeights: heights('#sidebar .nav-item'),
                };
            }""",
            route,
        )
        width_tolerance = 2
        self.assertLessEqual(
            metrics["documentScrollWidth"],
            metrics["rootClientWidth"] + width_tolerance,
            f"{route} document overflows horizontally: {metrics}",
        )
        self.assertLessEqual(
            metrics["bodyScrollWidth"],
            metrics["rootClientWidth"] + width_tolerance,
            f"{route} body overflows horizontally: {metrics}",
        )
        self.assertLessEqual(
            metrics["mainScrollWidth"],
            metrics["mainClientWidth"] + width_tolerance,
            f"{route} main overflows horizontally: {metrics}",
        )
        self.assertEqual([], metrics["overflowOffenders"], f"{route} has unconstrained text/control overflow: {metrics}")
        for key in ("buttonHeights", "filterHeights"):
            values = metrics[key]
            if values:
                self.assertGreaterEqual(min(values), 26, f"{route} {key} below desktop control height: {metrics}")
                self.assertLessEqual(max(values), 44, f"{route} {key} above stable desktop control height: {metrics}")
        field_heights = metrics["fieldHeights"]
        if field_heights:
            self.assertGreaterEqual(min(field_heights), 30, f"{route} field controls below desktop height: {metrics}")
            self.assertLessEqual(max(field_heights), 40, f"{route} field controls above stable desktop height: {metrics}")
        nav_heights = metrics["navHeights"]
        self.assertTrue(nav_heights, f"{route} sidebar controls were not measured")
        self.assertGreaterEqual(min(nav_heights), 26, f"{route} sidebar controls below desktop height: {metrics}")
        self.assertLessEqual(max(nav_heights), 44, f"{route} sidebar controls above stable desktop height: {metrics}")

    def _read_snapshot(self) -> dict[str, object]:
        with urlopen(f"{self.server_url}/api/status", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _capture_screenshot(self, page, name: str) -> Path:
        screenshot_dir = self._tmp_root / "web_console_screenshots" / self._testMethodName
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / name
        page.screenshot(path=path.as_posix(), full_page=True)
        self.assertTrue(path.exists() and path.stat().st_size > 0, f"Screenshot was not written: {path}")
        return path

    def _stop_overlay_focus_state(self, page) -> dict[str, str]:
        return page.evaluate(
            """() => {
                const overlay = document.querySelector("[data-overlay='stop']");
                const controls = overlay ? Array.from(overlay.querySelectorAll('[data-runner-option-toggle], [data-runner-option-mode], [data-runner-option-field], [data-stop-confirmation], [data-stop-close], [data-stop-confirm]')) : [];
                const isEnabled = (el) => Boolean(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                const keyFor = (el) => {
                    if (!el) return '';
                    if (el.matches('[data-runner-option-toggle]')) return `runner-option-toggle:${el.dataset.runnerOptionToggle || ''}`;
                    if (el.matches('[data-runner-option-mode]')) return `runner-option-mode:${el.dataset.runnerOptionMode || ''}`;
                    if (el.matches('[data-runner-option-field]')) return `runner-option-field:${el.dataset.runnerOptionField || ''}`;
                    if (el.matches('[data-stop-confirmation]')) return 'stop-confirmation';
                    if (el.matches('[data-stop-close]')) return 'stop-close';
                    if (el.matches('[data-stop-confirm]')) return 'stop-confirm';
                    return '';
                };
                const firstEnabled = controls.find(isEnabled) || null;
                return {
                    active: keyFor(document.activeElement),
                    firstEnabled: keyFor(firstEnabled),
                };
            }"""
        )

    def _assert_stop_overlay_focuses_first_enabled_control(self, page) -> None:
        focus_state = self._stop_overlay_focus_state(page)
        self.assertTrue(focus_state["firstEnabled"], focus_state)
        self.assertEqual(focus_state["firstEnabled"], focus_state["active"], focus_state)

    def _apply_snapshot_model(self, page, snapshot: dict[str, object]) -> None:
        page.evaluate("(model) => window.__AGENTCLI_ADAPTERS__.applySnapshotModel(model)", snapshot)

    def test_primary_desktop_routes_have_no_horizontal_overflow_and_stable_controls(self) -> None:
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

            page.set_viewport_size({"width": 1440, "height": 1024})
            page.locator('#topbar [data-action="set-locale-ko"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "ko")
            self._capture_screenshot(page, "desktop-dashboard-ko.png")

            for route in self.DESKTOP_PRIMARY_ROUTES:
                page.locator(f'#sidebar [data-nav="{route}"]').click()
                self.expect(page.locator("#main")).to_have_attribute("data-view", route)
                if route == "prompts":
                    self.expect(page.locator("[data-prompt-editor-root]")).to_have_attribute("data-prompt-loading", "false")
                if route == "pipeline":
                    self._capture_screenshot(page, "desktop-pipeline-ko.png")
                self._assert_desktop_route_layout(page, route)

            page.locator('#topbar [data-action="open-palette"]').click()
            palette = page.locator("[data-overlay='palette']")
            self.expect(palette).to_be_visible()
            self._capture_screenshot(page, "desktop-palette-ko.png")
            page.keyboard.press("Escape")
            self.expect(palette).to_be_hidden()
        finally:
            self._close_playwright(manager)

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
            self.expect(page.locator("#main .view").first).to_have_attribute("data-route-state", re.compile("ready|partial|empty|disabled|stale|loading|fallback|reconnecting|backend-unavailable|permission-denied|error"))
            self.expect(page.locator("#main")).to_contain_text("Pipeline snapshot")
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-stop')")
            stop_overlay = page.locator("[data-overlay='stop']")
            self.expect(stop_overlay).to_be_visible()
            self.expect(stop_overlay).to_contain_text('Type "STOP RUNNER" exactly to enable Stop.')
            self.expect(stop_overlay.locator("[data-stop-confirm]")).to_be_visible()
            self.expect(stop_overlay.locator("[data-stop-confirm]")).to_have_attribute("data-action-state", re.compile("confirmation|disabled|busy|retry|failure"))
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(stop_overlay).to_be_hidden()

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Dashboard")
            self.expect(page.locator("#main")).to_contain_text("Current task id")

            page.locator('#topbar [data-action="set-locale-ko"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "ko")
            self.expect(page.get_by_role("button", name="대시보드")).to_be_visible()
            self.expect(page.locator("#main h1.view__title")).to_have_text("대시보드")
            self.expect(page.locator(".topbar__status")).to_contain_text("완료")

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Dashboard")

            self._open_view(page, "nav-pipeline", "pipeline", "Stage lane")
            self._open_view(page, "nav-logs", "logs", "Live tail")
            self._open_view(page, "nav-backlog", "backlog", "Work queue")
            self._open_view(page, "nav-goals", "goals", "GOALS.md snapshot")

            self._open_view(page, "nav-config", "config", "Field details")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Config")
            self.expect(page.get_by_role("button", name="Save Changes")).to_be_visible()
            page.locator('#topbar [data-action="set-locale-ko"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "ko")
            self.expect(page.locator("#main h1.view__title")).to_have_text("설정")
            self.expect(page.get_by_role("button", name="변경 사항 저장")).to_be_visible()
            self.expect(page.get_by_role("button", name="초안 초기화")).to_be_visible()
            self.expect(page.locator("#main")).to_contain_text("필드 세부 정보")
            self.expect(page.locator("#main")).to_contain_text("비밀")
            page.locator('[data-config-select="iterations"]').click()
            page.locator('[data-config-field="iterations"]').fill("0")
            self.expect(page.locator(".config-detail")).to_contain_text("로컬 검증 실패")
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('save-config')")
            self.expect(page.locator(".config-save-state").first).to_contain_text("설정 저장 실패")
            self.expect(page.locator(".config-save-state").first).to_have_attribute("data-action-state", "failure")

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Config")

            self._open_view(page, "nav-prompts", "prompts", "Prompt inventory")
            self.expect(page.locator('[data-prompt-editor-root]')).to_have_attribute("data-prompt-loading", "false")
            self.expect(page.locator("#main")).to_contain_text("FULL READ PREVIEW")
            self.expect(page.get_by_role("button", name="Save Prompt")).to_be_visible()
            self.expect(page.get_by_role("button", name="Restore Backup")).to_be_visible()
            page.locator('#topbar [data-action="set-locale-ko"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "ko")
            self.expect(page.locator("#main h1.view__title")).to_have_text("프롬프트")
            self.expect(page.get_by_role("button", name="프롬프트 저장")).to_be_visible()
            self.expect(page.get_by_role("button", name="백업 복원")).to_be_visible()
            self.expect(page.locator("#main")).to_contain_text("추적 중인 프롬프트 역할")
            original_prompt_content = page.locator('[data-prompt-editor-field="content"]').input_value()
            page.locator('[data-prompt-editor-field="content"]').fill("")
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('prompt-save')")
            self.expect(page.locator('[data-prompt-editor-state]')).to_contain_text("저장 오류")
            self.expect(page.locator('[data-prompt-editor-banner]')).to_contain_text("프롬프트 저장 실패")
            self.expect(page.locator(".prompt-mutation-state")).to_have_attribute("data-action-state", "failure")
            self.expect(page.locator('[data-prompt-editor-validation]')).to_contain_text("프롬프트 내용은 비워둘 수 없습니다.")
            page.locator('[data-prompt-editor-field="content"]').fill(original_prompt_content)
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('prompt-restore')")
            self.expect(page.locator('[data-prompt-editor-state]')).to_contain_text("복원 오류")
            self.expect(page.locator('[data-prompt-editor-banner]')).to_contain_text("프롬프트 복원 실패")
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-stop')")
            stop_overlay = page.locator("[data-overlay='stop']")
            self.expect(stop_overlay).to_be_visible()
            self.expect(stop_overlay).to_contain_text('중지 작업을 활성화하려면 "STOP RUNNER"를 정확히 입력하세요.')
            self.expect(stop_overlay.locator("[data-stop-confirm]")).to_be_visible()
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(stop_overlay).to_be_hidden()

            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-reload')")
            reload_overlay = page.locator("[data-overlay='stop']")
            self.expect(reload_overlay).to_be_visible()
            self.expect(reload_overlay).to_contain_text('다시 불러오기 작업을 활성화하려면 "RELOAD RUNNER"를 정확히 입력하세요.')
            self.expect(reload_overlay.locator("[data-stop-confirm]")).to_be_visible()
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(reload_overlay).to_be_hidden()

            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-restart')")
            restart_overlay = page.locator("[data-overlay='stop']")
            self.expect(restart_overlay).to_be_visible()
            self.expect(restart_overlay).to_contain_text('재시작 작업을 활성화하려면 "RESTART RUNNER"를 정확히 입력하세요.')
            self.expect(restart_overlay.locator("[data-stop-confirm]")).to_be_visible()
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(restart_overlay).to_be_hidden()

            self._open_view(page, "nav-logs", "logs", "로그")
            self.expect(page.locator("#main h1.view__title")).to_have_text("로그")
            self.expect(page.get_by_role("button", name="전체")).to_be_visible()
            self.expect(page.get_by_role("button", name="정보")).to_be_visible()
            self.expect(page.get_by_role("button", name="경고")).to_be_visible()
            self.expect(page.get_by_role("button", name="오류")).to_be_visible()
            self.expect(page.get_by_role("button", name="디버그")).to_be_visible()
            self.expect(page.locator('[data-log-filter-field="stage"]')).to_have_attribute("placeholder", "단계")
            self.expect(page.locator('[data-log-filter-field="taskId"]')).to_have_attribute("placeholder", "작업 ID")
            self.expect(page.locator('[data-log-filter-field="search"]')).to_have_attribute("placeholder", "검색")
            self.expect(page.get_by_role("button", name="선택한 줄 복사")).to_be_visible()
            self.expect(page.get_by_role("button", name="필터된 로그 다운로드")).to_be_visible()
            self.expect(page.get_by_role("button", name="선택 해제")).to_be_visible()
            logs_toggle = page.locator('[data-action="toggle-logs"]').first
            self.expect(logs_toggle).to_have_text("라이브 tail 일시정지")
            logs_toggle.click()
            self.expect(logs_toggle).to_have_text("라이브 tail 재개")

            self._open_view(page, "nav-worktree", "worktree", "대기 중인 작업트리 병합")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Worktree Review")
            self.expect(page.locator("#main")).to_contain_text("대기 중인 작업트리 병합")
            self.expect(page.locator("#main")).to_contain_text("정리 상태")
            self.expect(page.locator("#main")).to_contain_text("위험 참고")
            self.expect(page.locator('[data-action="worktree-apply"]').first).to_be_visible()
            self.expect(page.locator('[data-action="worktree-discard"]').first).to_be_visible()
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('worktree-apply')")
            worktree_overlay = page.locator("[data-overlay='worktree-action']")
            self.expect(worktree_overlay).to_be_visible()
            self.expect(worktree_overlay).to_contain_text("MERGE WORKTREE")
            self.expect(worktree_overlay.locator("[data-worktree-action-confirm]")).to_be_visible()
            self.expect(worktree_overlay.locator("[data-worktree-action-confirm]")).to_have_attribute("data-action-state", "confirmation")
            page.keyboard.press("Escape")
            self.expect(worktree_overlay).to_be_hidden()
            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('worktree-discard')")
            discard_overlay = page.locator("[data-overlay='worktree-action']")
            self.expect(discard_overlay).to_be_visible()
            self.expect(discard_overlay).to_contain_text("DISCARD WORKTREE")
            self.expect(discard_overlay.locator("[data-worktree-action-confirm]")).to_be_visible()
            page.keyboard.press("Escape")
            self.expect(discard_overlay).to_be_hidden()

            page.locator('#topbar [data-action="set-locale-en"]').click()
            self.expect(page.locator("html")).to_have_attribute("lang", "en")

            self._open_view(page, "nav-landing", "landing", "Direction A")
            page.set_viewport_size({"width": 390, "height": 844})
            self._open_view(page, "nav-mobile", "mobile", "Mobile workflow")

            mobile_root = page.locator("[data-mobile-workflow-root]")
            self.expect(mobile_root).to_be_visible()
            self.expect(mobile_root.locator(".runner-control-panel")).to_be_visible()
            self.expect(mobile_root.locator("[data-mobile-route-grid]")).to_be_visible()
            self.expect(mobile_root.locator("[data-mobile-filter-panel]")).to_be_visible()
            self.expect(mobile_root.locator("[data-mobile-editor-panel]")).to_be_visible()
            self.expect(mobile_root.locator("[data-mobile-confirmation-panel]")).to_be_visible()
            self.expect(mobile_root.locator("[data-mobile-notification-panel]")).to_be_visible()
            self.assertEqual(10, mobile_root.locator("[data-mobile-route-grid] [data-nav]").count())
            self._capture_screenshot(page, "mobile-workflow-en.png")

            mobile_root.locator('[data-mobile-route-grid] [data-nav="logs"]').click()
            self.expect(page.locator("#main")).to_have_attribute("data-view", "logs")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Logs")

            page.locator('#sidebar [data-nav="mobile"]').click()
            self.expect(page.locator("#main")).to_have_attribute("data-view", "mobile")
            mobile_root = page.locator("[data-mobile-workflow-root]")

            layout = page.evaluate(
                """() => {
                    const visible = (el) => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                    };
                    const rects = (selector) => Array.from(document.querySelectorAll(selector))
                        .filter(visible)
                        .map((el) => {
                            const rect = el.getBoundingClientRect();
                            return {
                                top: Math.round(rect.top * 10) / 10,
                                bottom: Math.round(rect.bottom * 10) / 10,
                                left: Math.round(rect.left * 10) / 10,
                                right: Math.round(rect.right * 10) / 10,
                                width: Math.round(rect.width * 10) / 10,
                                height: Math.round(rect.height * 10) / 10,
                                text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                            };
                        });
                    return {
                        panels: rects('[data-mobile-workflow-root] .panel'),
                        routeButtons: rects('[data-mobile-route-grid] [data-nav]'),
                        editorButtons: rects('[data-mobile-editor-panel] .button'),
                        confirmButtons: rects('[data-mobile-confirmation-panel] .button'),
                        runnerButtons: rects('[data-mobile-workflow-root] .runner-control__buttons .button'),
                    };
                }"""
            )

            route_heights = mobile_root.locator("[data-mobile-route-grid] [data-nav]").evaluate_all(
                """(els) => els.map((el) => el.getBoundingClientRect().height)"""
            )
            self.assertTrue(route_heights)
            self.assertGreaterEqual(min(route_heights), 58)
            self.assertEqual(10, len(layout["routeButtons"]))

            def assert_stack(name: str, rects: list[dict[str, object]]) -> None:
                ordered = sorted(rects, key=lambda rect: (float(rect["top"]), float(rect["left"])))
                for previous, current in zip(ordered, ordered[1:]):
                    self.assertLessEqual(
                        float(previous["bottom"]),
                        float(current["top"]) + 2,
                        f"{name} overlaps vertically: {ordered}",
                    )

            assert_stack("mobile panels", layout["panels"])
            assert_stack("route buttons", layout["routeButtons"])
            assert_stack("editor buttons", layout["editorButtons"])
            assert_stack("confirmation buttons", layout["confirmButtons"])
            assert_stack("runner buttons", layout["runnerButtons"])

            mobile_root.locator('[data-mobile-editor-panel] [data-action="nav-goals"]').click()
            self.expect(page.locator("#main")).to_have_attribute("data-view", "goals")
            self.expect(page.locator("#main h1.view__title")).to_have_text("Goals")

            page.locator('#sidebar [data-nav="mobile"]').click()
            self.expect(page.locator("#main")).to_have_attribute("data-view", "mobile")
            mobile_root = page.locator("[data-mobile-workflow-root]")

            filter_input = mobile_root.locator('[data-mobile-filter-panel] [data-log-filter-field="search"]')
            self.expect(filter_input).to_be_visible()
            filter_input.fill("smoke")
            self.expect(filter_input).to_have_value("smoke")

            mobile_root.locator('[data-mobile-confirmation-panel] [data-action="runner-stop"]').click()
            stop_overlay = page.locator("[data-overlay='stop']")
            self.expect(stop_overlay).to_be_visible()
            self.expect(stop_overlay).to_contain_text('STOP RUNNER')
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(stop_overlay).to_be_hidden()

            mobile_root.locator('[data-mobile-confirmation-panel] [data-action="worktree-apply"]').click()
            worktree_overlay = page.locator("[data-overlay='worktree-action']")
            self.expect(worktree_overlay).to_be_visible()
            self.expect(worktree_overlay).to_contain_text('MERGE WORKTREE')
            page.keyboard.press("Escape")
            self.expect(worktree_overlay).to_be_hidden()

            dimensions = page.evaluate(
                """() => ({
                    innerWidth: window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    confirmHeights: Array.from(document.querySelectorAll('[data-mobile-confirmation-panel] .button')).map((el) => el.getBoundingClientRect().height),
                })"""
            )
            self.assertLessEqual(dimensions["scrollWidth"], dimensions["innerWidth"])
            self.assertGreaterEqual(min(dimensions["confirmHeights"]), 34)
            self._capture_screenshot(page, "mobile-workflow-390.png")

            page.locator('#sidebar [data-nav="dashboard"]').click()
            self.expect(page.locator("#main")).to_have_attribute("data-view", "dashboard")
            self.expect(page.locator(".topbar__status")).to_be_hidden()
        finally:
            self._close_playwright(manager)

    def test_keyboard_navigation_and_accessibility_flows(self) -> None:
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

            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-stop')")
            stop_overlay = page.locator("[data-overlay='stop']")
            self.expect(stop_overlay).to_be_visible()
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(stop_overlay).to_be_hidden()

            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-reload')")
            reload_overlay = page.locator("[data-overlay='stop']")
            self.expect(reload_overlay).to_be_visible()
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(reload_overlay).to_be_hidden()

            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-restart')")
            restart_overlay = page.locator("[data-overlay='stop']")
            self.expect(restart_overlay).to_be_visible()
            self._assert_stop_overlay_focuses_first_enabled_control(page)
            page.keyboard.press("Escape")
            self.expect(restart_overlay).to_be_hidden()

            snapshot = self._read_snapshot()
            for runner_key in ("runnerControl", "runner_control"):
                runner_control = snapshot.get(runner_key)
                if isinstance(runner_control, dict):
                    runner_control["enabled"] = False
                    runner_control["message"] = "Runner controls are disabled."
            for live_run_key in ("liveRun", "live_run"):
                live_run = snapshot.get(live_run_key)
                if not isinstance(live_run, dict):
                    continue
                for runner_key in ("runnerControl", "runner_control", "control"):
                    live_runner_control = live_run.get(runner_key)
                    if isinstance(live_runner_control, dict):
                        live_runner_control["enabled"] = False
                        live_runner_control["message"] = "Runner controls are disabled."
            self._apply_snapshot_model(page, snapshot)
            self.expect(page.locator('#topbar [data-action="runner-restart"]')).to_be_disabled()

            page.evaluate("window.__AGENTCLI_ADAPTERS__.handleAction('runner-restart')")
            disabled_overlay = page.locator("[data-overlay='stop']")
            self.expect(disabled_overlay).to_be_visible()
            self.expect(disabled_overlay).to_contain_text("Runner controls are disabled.")
            self.expect(disabled_overlay.locator("[data-stop-confirmation]")).to_be_disabled()
            self.expect(page.get_by_role("button", name="Cancel")).to_be_focused()
            page.keyboard.press("Escape")
            self.expect(disabled_overlay).to_be_hidden()
        finally:
            self._close_playwright(manager)

    def test_worktree_review_smoke_surfaces_cleanup_and_diagnostic_states(self) -> None:
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

            snapshot = self._read_snapshot()
            cleanup_worktree = self._tmp / "cleanup-failed-smoke"
            orphaned_worktree = self._tmp / "orphaned-smoke"
            locked_path = (cleanup_worktree / "nested" / "locked.txt").as_posix()
            run_dir_text = self.latest_run_dir.as_posix()
            patch_path = (self.latest_run_dir / "worktree.patch").as_posix()
            status_file = (self.latest_run_dir / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").as_posix()
            pending_file = (self.latest_run_dir / "WORKTREE_MERGE_PENDING.json").as_posix()

            snapshot["worktree"] = {
                "status": "applied_cleanup_failed",
                "mode": "manual",
                "reviewRequired": True,
                "reviewRequiredMessage": "Patch applied, but worktree cleanup failed.",
                "sourceRepo": self.repo.as_posix(),
                "sourceBranch": "main",
                "branch": "main",
                "baseRef": "main",
                "headRef": "abc12345",
                "worktreeDir": cleanup_worktree.as_posix(),
                "patchPath": patch_path,
                "pendingFile": pending_file,
                "statusFile": status_file,
                "cleanupPath": locked_path,
                "cleanupMessage": "Permission denied while removing the generated worktree.",
                "cleanupDetails": {
                    "path": locked_path,
                    "lockingPath": locked_path,
                    "affectedArtifact": cleanup_worktree.as_posix(),
                    "retrySchedule": [0.05, 0.1, 0.2],
                    "rebootGuidance": "Close the locking process or reboot Windows before retrying cleanup.",
                },
                "cleanupAttempts": [
                    {
                        "attempt": 1,
                        "lockingPath": locked_path,
                        "affectedArtifact": cleanup_worktree.as_posix(),
                    }
                ],
                "cleanupReconciliation": {
                    "artifactPath": status_file,
                    "artifactStatus": "applied_cleanup_failed",
                    "finalStatus": "applied",
                    "worktreeDir": cleanup_worktree.as_posix(),
                    "worktreeExists": True,
                    "cleanupPath": locked_path,
                    "cleanupPathExists": False,
                    "pendingMarkerPaths": [],
                    "existingPendingMarkers": [],
                    "markerState": "reconciled",
                    "worktreeState": "present",
                    "blockingPaths": [cleanup_worktree.as_posix()],
                    "reconciled": False,
                    "reconciledFrom": "",
                },
                "cleanupState": "failed",
                "resolutionActions": [
                    {
                        "kind": "generated_worktree_remove",
                        "status": "failed",
                        "path": cleanup_worktree.as_posix(),
                        "detail": "Permission denied while removing the generated worktree.",
                    },
                    {
                        "kind": "stale_marker_prune",
                        "status": "done",
                        "path": pending_file,
                        "detail": "Pending marker paths were cleared after the worktree result was finalized.",
                    },
                    {
                        "kind": "cleanup_failed_reconcile",
                        "status": "required",
                        "path": status_file,
                        "detail": f"Still blocked by: {cleanup_worktree.as_posix()}",
                    },
                ],
                "summary": "Patch applied, but worktree cleanup failed.",
                "risk": "The source repository was updated, but the isolated worktree still needs cleanup.",
                "changedFiles": [
                    {
                        "path": "web_console/app.js",
                        "summary": "Updated worktree cleanup review details.",
                        "kind": "modified",
                        "hunks": [],
                        "lineCount": 0,
                    }
                ],
                "preflight": {},
                "applyCheck": {},
                "sourceRepoDirty": False,
                "checklist": ["Inspect patch hunks", "Verify no secret leakage"],
                "runDir": run_dir_text,
                "runnerRc": 0,
                "lastRc": 0,
            }
            snapshot["worktree_diagnostics"] = {
                "status": "error",
                "source_repo": self.repo.as_posix(),
                "source_repo_root": self.repo.as_posix(),
                "generated_worktree_home": (self._tmp / ".agentcli_worktrees" / self.repo.name).as_posix(),
                "scanned_at": "2026-04-26T12:05:00",
                "summary": {
                    "run_dirs_scanned": 1,
                    "pending_markers": 1,
                    "stale_pending_markers": 1,
                    "missing_patches": 0,
                    "cleanup_failed": 1,
                    "generated_worktrees": 1,
                    "orphaned_worktrees": 1,
                    "issue_count": 3,
                    "healthy": False,
                    "category_counts": {
                        "active": 2,
                        "pending": 1,
                        "stale": 1,
                        "orphaned": 1,
                        "cleanup_failed": 1,
                        "missing_patch": 0,
                    },
                },
                "filters": {
                    "categories": [],
                    "available_categories": ["active", "pending", "stale", "orphaned", "cleanup_failed", "missing_patch"],
                },
                "pending_markers": [
                    {
                        "path": pending_file,
                        "scope": "repo",
                        "status": "pending",
                        "reason": "Central pending marker has no matching run-local marker.",
                        "run_dir": run_dir_text,
                        "source_repo": self.repo.as_posix(),
                        "worktree_dir": cleanup_worktree.as_posix(),
                        "patch_path": patch_path,
                        "base_ref": "main",
                        "head_ref": "abc12345",
                        "exists": True,
                        "stale": True,
                        "resolutionActions": [
                            {
                                "kind": "stale_marker_prune",
                                "status": "required",
                                "path": pending_file,
                                "detail": "Remove or repair the stale pending marker only after verifying the patch metadata.",
                            }
                        ],
                        "categories": ["pending", "stale"],
                    }
                ],
                "cleanup_failed": [
                    {
                        "path": status_file,
                        "status": "applied_cleanup_failed",
                        "run_dir": run_dir_text,
                        "source_repo": self.repo.as_posix(),
                        "worktree_dir": cleanup_worktree.as_posix(),
                        "patch_path": patch_path,
                        "cleanup_path": locked_path,
                        "cleanup_message": "Permission denied while removing the generated worktree.",
                        "cleanup_details": {
                            "locking_path": locked_path,
                            "affected_artifact": cleanup_worktree.as_posix(),
                            "retry_schedule": [0.05, 0.1, 0.2],
                            "reboot_guidance": "Close the locking process or reboot Windows before retrying cleanup.",
                        },
                        "cleanup_attempts": [],
                        "reconciliation": {
                            "artifact_path": status_file,
                            "artifact_status": "applied_cleanup_failed",
                            "final_status": "applied",
                            "blocking_paths": [cleanup_worktree.as_posix()],
                            "reconciled": False,
                        },
                        "resolutionActions": [
                            {
                                "kind": "generated_worktree_remove",
                                "status": "failed",
                                "path": cleanup_worktree.as_posix(),
                                "detail": "Permission denied while removing the generated worktree.",
                            },
                            {
                                "kind": "stale_marker_prune",
                                "status": "done",
                                "path": pending_file,
                                "detail": "Pending marker paths were cleared after the worktree result was finalized.",
                            },
                            {
                                "kind": "cleanup_failed_reconcile",
                                "status": "required",
                                "path": status_file,
                                "detail": f"Still blocked by: {cleanup_worktree.as_posix()}",
                            },
                        ],
                        "categories": ["cleanup_failed", "active"],
                    }
                ],
                "generated_worktrees": [
                    {
                        "path": orphaned_worktree.as_posix(),
                        "exists": True,
                        "contract_path": "",
                        "contract_run_dir": "",
                        "contract_status": "missing_contract",
                        "reason": "missing reuse contract",
                        "tracked": False,
                        "orphaned": True,
                        "referenced": False,
                        "resolutionActions": [
                            {
                                "kind": "generated_worktree_remove",
                                "status": "required",
                                "path": orphaned_worktree.as_posix(),
                                "detail": "Remove the orphaned generated worktree after verifying no active marker still references it.",
                            }
                        ],
                        "categories": ["orphaned"],
                    }
                ],
                "issues": [
                    {
                        "kind": "stale_pending_marker",
                        "severity": "warn",
                        "message": "Central pending marker has no matching run-local marker.",
                        "path": pending_file,
                        "categories": ["pending", "stale"],
                    },
                    {
                        "kind": "cleanup_failed",
                        "severity": "error",
                        "message": "Cleanup failed after the worktree result was finalized.",
                        "path": status_file,
                        "categories": ["cleanup_failed", "active"],
                    },
                    {
                        "kind": "orphaned_worktree",
                        "severity": "warn",
                        "message": "Generated worktree is no longer referenced by an active contract or pending marker.",
                        "path": orphaned_worktree.as_posix(),
                        "categories": ["orphaned"],
                    },
                ],
            }

            page.evaluate(
                """(snapshot) => {
                    const adapters = window.__AGENTCLI_ADAPTERS__;
                    const normalized = adapters.normalizeSnapshot(snapshot);
                    adapters.applySnapshotModel(normalized);
                    adapters.setView('worktree');
                    adapters.renderShell({ force: true, preserveScroll: false });
                }""",
                snapshot,
            )

            self.expect(page.locator("#main")).to_have_attribute("data-view", "worktree")
            self.expect(page.locator("#main")).to_contain_text("Worktree Review")
            self.expect(page.locator("#main")).to_contain_text("Cleanup actions")
            self.expect(page.locator("#main")).to_contain_text("Generated worktree removal")
            self.expect(page.locator("#main")).to_contain_text("Cleanup-failed reconciliation")
            self.expect(page.locator("#main")).to_contain_text("Stale marker pruning")
            self.expect(page.locator("#main")).to_contain_text("Locking path")
            self.expect(page.locator("#main")).to_contain_text(locked_path)
            self.expect(page.locator("#main")).to_contain_text("Affected artifact")
            self.expect(page.locator("#main")).to_contain_text(cleanup_worktree.as_posix())
            self.expect(page.locator("#main")).to_contain_text("Retry schedule")
            self.assertRegex(page.locator("#main").inner_text(), r"0\.05s,\s*0\.1(?:0)?s,\s*0\.2s")
            self.expect(page.locator("#main")).to_contain_text("Reboot guidance")
            self.expect(page.locator("#main")).to_contain_text("orphaned-smoke")
        finally:
            self._close_playwright(manager)

    def test_live_run_sequence_covers_stop_reconnect_and_completion(self) -> None:
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
            fixtures = deque(self._make_web_console_live_run_fixtures())
            observed_names: list[str] = []

            def before_goto(context, page) -> None:
                context.add_init_script(
                    "globalThis.__AGENTCLI_TEST_HOOKS__ = {"
                    " snapshotPollMs: 120000,"
                    " snapshotMaxBackoffMs: 240000,"
                    " snapshotStaleAfterMs: 240000"
                    " };"
                )

                def handle_status(route) -> None:
                    if not fixtures:
                        raise AssertionError("Unexpected /api/status request")
                    fixture = fixtures.popleft()
                    observed_names.append(str(fixture["name"]))
                    if fixture.get("kind") == "error":
                        route.fulfill(status=int(fixture["status"]), json=fixture["body"])
                        return
                    route.fulfill(json=fixture)

                context.route("**/api/status", handle_status)

            try:
                page = self._open_page(playwright, before_goto=before_goto)
            except Exception as exc:
                raise unittest.SkipTest(
                    "Playwright Chromium is unavailable. Optional setup: "
                    f'"{sys.executable}" -m pip install playwright && '
                    f'"{sys.executable}" -m playwright install chromium'
            ) from exc

            self.expect(page.locator("#main")).to_have_attribute("data-view", "dashboard")
            self.expect(page.locator(".topbar__status")).to_contain_text("Running")
            self.expect(page.locator("#main")).to_contain_text("No output for 20 minutes.")
            self.expect(page.locator("#main")).to_contain_text("Runner controls")
            self.expect(page.locator("#main")).to_contain_text("Running")

            page.evaluate("window.__AGENTCLI_ADAPTERS__.stopSnapshotPolling()")

            page.evaluate("window.__AGENTCLI_ADAPTERS__.refreshSnapshot()")
            self.expect(page.locator("#main")).to_contain_text("Stopping")
            self.expect(page.locator("#main")).to_contain_text("Current phase")
            self.expect(page.locator("#main")).to_contain_text("Requested")
            self.expect(page.locator("#main")).to_contain_text("Phase history")
            self.expect(page.locator("#main")).to_contain_text("Stop requested; draining child processes.")

            page.evaluate("window.__AGENTCLI_ADAPTERS__.refreshSnapshot()")
            self._open_view(page, "nav-pipeline", "pipeline", "Stage lane")
            self.expect(page.locator("#main")).to_contain_text("Stopped")
            self.expect(page.locator("#main")).to_contain_text("Runner stopped cleanly.")
            self.expect(page.locator("#main")).to_contain_text("Stop finalized.")

            page.evaluate("window.__AGENTCLI_ADAPTERS__.refreshSnapshot()")
            reconnect_state = page.evaluate("window.__AGENTCLI_ADAPTERS__.inspectSnapshotRefreshState()")
            self.assertEqual("reconnecting", reconnect_state["status"])
            self.assertEqual(503, reconnect_state["lastErrorStatus"])
            self.assertIn("HTTP 503", reconnect_state["lastError"])
            self.assertEqual(240000, reconnect_state["retryDelayMs"])
            self.expect(page.locator(".status-chip--snapshot")).to_contain_text("Reconnecting")

            page.evaluate("window.__AGENTCLI_ADAPTERS__.refreshSnapshot()")
            stale_state = page.evaluate("window.__AGENTCLI_ADAPTERS__.inspectSnapshotRefreshState()")
            self.assertTrue(stale_state["stale"])
            self.expect(page.locator(".status-chip--snapshot")).to_contain_text("Stale snapshot")
            self._open_view(page, "nav-logs", "logs", "Live tail")
            self.expect(page.locator(".status-chip--snapshot")).to_contain_text("Stale snapshot")

            page.evaluate("window.__AGENTCLI_ADAPTERS__.refreshSnapshot()")
            self._open_view(page, "nav-dashboard", "dashboard", "Complete")
            self.expect(page.locator("#main")).to_contain_text("Complete")
            self.expect(page.locator("#main")).to_contain_text("Runner idle.")
            self.expect(page.locator("#main")).to_contain_text("Run completed successfully.")

            self._open_view(page, "nav-pipeline", "pipeline", "Stage lane")
            self.expect(page.locator("#main")).to_contain_text("Completed")
            self.expect(page.locator("#main")).to_contain_text("QA verification completed.")

            self._open_view(page, "nav-notifications", "notifications", "Task done")
            self.expect(page.locator("#main")).to_contain_text("Task done")
            self.expect(page.locator("#main")).to_contain_text("T-020 | QA verification completed")
            self.assertEqual(
                [
                    "running-long",
                    "stop-requested",
                    "stop-finalized",
                    "reconnect-error",
                    "stale-reconnect",
                    "completed-run",
                ],
                observed_names,
            )
        finally:
            self._close_playwright(manager)


if __name__ == "__main__":
    unittest.main()
