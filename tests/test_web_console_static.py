from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


class WebConsoleStaticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index_html = read_text(WEB_CONSOLE / "index.html")
        self.styles_css = read_text(WEB_CONSOLE / "styles.css")
        self.app_js = read_text(WEB_CONSOLE / "app.js")

    def test_index_uses_only_local_assets(self) -> None:
        script_sources = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', self.index_html, flags=re.I)

        self.assertEqual(script_sources, ["./app.js"])
        self.assertIn('<link rel="stylesheet" href="./styles.css">', self.index_html)
        self.assertIn('id="app"', self.index_html)

        lowered = self.index_html.lower()
        self.assertNotIn("react", lowered)
        self.assertNotIn("babel", lowered)
        self.assertNotIn("docs/design/project", lowered)
        self.assertNotIn("text/babel", lowered)

    def test_styles_lock_direction_a_shell_geometry(self) -> None:
        required_tokens = [
            "--topbar-h: 44px",
            "--sidebar-w: 220px",
            "--radius-1: 2px",
            "--radius-2: 3px",
            "--radius-3: 4px",
            '--mono: "JetBrains Mono"',
            '--sans: "Inter"',
            ".app-shell",
            ".topbar",
            ".sidebar",
            ".main",
            ".overlay",
            ".section-banner",
            ".section-banner--warn",
            ".section-banner--err",
            ".section-banner--info",
            ".section-banner--idle",
            ".section-banner--success",
            ".section-banner--completed",
            ".section-banner--stopped",
            ".section-banner--failed",
            ".section-banner__title",
            ".section-banner__copy",
            ".palette-input",
            ".modal-actions",
            ".board-grid--four",
            ".kpi-grid--four",
            ".stage-card--failed",
            ".stage-card--stopped",
            ".stage-icon--failed",
            ".stage-icon--stopped",
            ".connector--warn",
            ".chip--err",
            ".meter-chip--unavailable",
            ".meter__fill--muted",
            ".stat-card__value--unavailable",
            ".kpi-card__value--unavailable",
            "overflow: auto",
        ]

        for token in required_tokens:
            self.assertIn(token, self.styles_css)

    def test_app_js_defines_required_shell_views_and_keyboard_flows(self) -> None:
        required_views = [
            "Dashboard",
            "Pipeline",
            "Logs",
            "Backlog",
            "Goals",
            "Config",
            "Prompts",
            "Run History",
            "Notifications",
            "Worktree Review",
            "Landing preview",
            "Mobile preview",
        ]
        required_function_names = [
            "renderDashboard",
            "renderPipeline",
            "renderLogs",
            "renderBacklog",
            "renderGoals",
            "renderConfig",
            "renderPrompts",
            "renderHistory",
            "renderNotifications",
            "renderWorktree",
            "renderLanding",
            "renderMobile",
            "adaptActiveRun",
            "adaptStages",
            "adaptBacklog",
            "adaptGoals",
            "adaptConfig",
            "adaptPrompts",
            "adaptLogs",
            "adaptNotifications",
            "adaptMetrics",
            "adaptHistory",
            "adaptWorktree",
            "normalizeSnapshot",
            "createBlankModel",
            "createFallbackFixture",
            "refreshSnapshot",
            "startSnapshotPolling",
            "startFallbackLogStream",
            "sectionNotice",
        ]
        required_keyboard_tokens = [
            "openPalette",
            "openStopModal",
            "closeStopModal",
            "refresh-status",
            "state.pendingChord = 'g'",
            "event.key === 'Escape'",
            "g d",
            "g p",
            "g l",
            "g b",
            "g g",
            "g c",
            "g t",
            "g r",
            "g n",
            "g w",
            "g h",
            "g m",
        ]
        required_shell_tokens = [
            "__AGENTCLI_ADAPTERS__",
            "SNAPSHOT_POLL_MS",
            "MAX_LOG_ROWS",
            "fetch('/api/status'",
            "Fallback data",
            "Loading read-only snapshot",
            "Partial snapshot",
            "Stale snapshot",
            "status-chip--running",
            "status-chip--idle",
            "status-chip--success",
            "status-chip--completed",
            "status-chip--stopped",
            "status-chip--failed",
            "meter__fill",
            "meter-chip--unavailable",
            "meter__fill--muted",
            "stat-card__value--unavailable",
            "kpi-card__value--unavailable",
            "section-banner--idle",
            "section-banner--success",
            "section-banner--completed",
            "section-banner--stopped",
            "section-banner--failed",
            "Current task id",
            "Current task title",
            "Run directory",
            "Final reason",
            "GOALS.md snapshot",
            "Raw text preview",
            "Parser warnings",
            "Source line",
            "Changes stay in browser storage until the save workflow lands.",
            "token telemetry unavailable",
            "data-overlay",
            "data-palette-input",
            "data-stop-confirm",
            "renderMainView",
            "VIEW_LABELS",
        ]

        for token in required_views + required_function_names + required_keyboard_tokens + required_shell_tokens:
            self.assertIn(token, self.app_js)

        lowered = self.app_js.lower()
        self.assertNotIn("reactdom", lowered)
        self.assertNotIn("react-dom", lowered)
        self.assertNotIn("babel/standalone", lowered)
        self.assertNotIn("docs/design/project", lowered)
        self.assertNotIn("text/babel", lowered)

    def test_required_views_have_nonblank_render_content(self) -> None:
        render_markers = {
            "renderDashboard": ["Pipeline snapshot", "Run facts", "Current task id", "Run directory", "Final reason", "No log entries yet.", "No goals published yet."],
            "renderPipeline": ["Stage lane", "Current stage output", "token telemetry unavailable", "Only some lifecycle records were published.", "No lifecycle records were published yet.", "Recent output unavailable."],
            "renderLogs": ["Tail filter", "No log entries match the current filter.", "Read-only snapshot"],
            "renderBacklog": ["Work queue", "No task selected.", "No backlog artifacts were published yet.", "Dependencies unavailable", "File scope unavailable", "Failure unavailable", "Recent output unavailable."],
            "renderGoals": ["Goal progress", "Loading the read-only snapshot...", "GOALS.md snapshot", "Raw text preview", "Parser warnings", "Read-only GOALS.md snapshot with browser-local edits ready for later save workflow."],
            "renderConfig": ["field details", "Loading read-only snapshot", "restart required"],
            "renderPrompts": ["Prompt pack", "No prompt files were discovered.", "No prompt selected"],
            "renderHistory": ["Run history", "No run history yet.", "Selected run"],
            "renderNotifications": ["Event feed", "No notifications yet.", "Notification counts"],
            "renderWorktree": ["Pending merge", "No pending worktree merge is available.", "Review checklist"],
            "renderLanding": ["Direction A landing preview", "No Babel in browser, no React CDN, no docs/Design runtime imports."],
            "renderMobile": ["phone-frame", "No notifications yet.", "Telegram-style remote view"],
        }

        for function_name, markers in render_markers.items():
            self.assertIn(f"function {function_name}", self.app_js)
            for marker in markers:
                self.assertIn(marker, self.app_js)


if __name__ == "__main__":
    unittest.main()
