from __future__ import annotations

import inspect

import agent_runner.progress as progress
import agent_runner.utils as utils
from agent_runner.backends import claude_quota, claudecode, codex_quota, codex_runner


def test_backend_specific_quota_implementations_live_under_backend_modules() -> None:
    assert codex_runner.check_codex_quota_utilization is codex_quota.check_codex_quota_utilization
    assert claudecode.check_quota_utilization is claude_quota.check_quota_utilization
    assert claudecode.seconds_until_reset is claude_quota.seconds_until_reset


def test_shared_utils_does_not_own_backend_specific_quota_or_app_server_code() -> None:
    forbidden_attrs = {
        "_CodexAppServerClient",
        "CodexAppServerError",
        "CodexRateLimitWindow",
        "check_codex_quota_utilization",
        "parse_codex_rate_limit_windows",
        "seconds_until_unix_reset",
        "check_quota_utilization",
        "fetch_quota_usage",
        "_load_oauth_token",
        "seconds_until_reset",
        "extract_codex_tokens",
        "extract_claude_tokens",
    }
    for name in forbidden_attrs:
        assert not hasattr(utils, name), f"backend-specific helper leaked into agent_runner.utils: {name}"

    source = inspect.getsource(utils)
    forbidden_snippets = (
        "codex app-server",
        "api.anthropic.com/api/oauth/usage",
        "rateLimitsByLimitId",
        ".claude/.credentials.json",
    )
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_shared_progress_does_not_own_backend_specific_token_extractors() -> None:
    assert not hasattr(progress, "extract_codex_tokens")
    assert not hasattr(progress, "extract_claude_tokens")

    source = inspect.getsource(progress)
    assert "Codex RunResult" not in source
    assert "Claude SDK structured response" not in source
