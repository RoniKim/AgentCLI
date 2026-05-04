from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_runner.state import (
    append_state_warning,
    load_state,
    mark_state_task_done,
    save_state,
)
from agent_runner.task_failures import record_task_failure_state


def _failed_tasks(state: dict) -> list[str]:
    return [str(item.get("task") or "") for item in state.get("failed", []) if isinstance(item, dict)]


def _warning_reasons(state: dict) -> list[str]:
    return [str(item.get("reason") or "") for item in state.get("warnings", []) if isinstance(item, dict)]


@contextmanager
def _temp_state_path():
    configured = str(os.environ.get("AGENTCLI_TEST_TMP") or "").strip()
    base = Path(configured) if configured else Path.cwd() / ".tmp-tests" / "state_concurrency"
    base.mkdir(parents=True, exist_ok=True)
    temp_dir = base / f"state-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        yield temp_dir / "STATE.json"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_stale_failed_writer_and_stale_warning_writer_are_both_preserved() -> None:
    with _temp_state_path() as state_path:
        failed_writer = load_state(state_path)
        warning_writer = load_state(state_path)

        record_task_failure_state(
            failed_writer,
            task_id="T1",
            reason="build_failed",
            detail="compiler failed",
        )
        save_state(state_path, failed_writer)

        warning_writer.setdefault("warnings", []).append(
            {"task": "T2", "reason": "max_turns_exceeded", "detail": "stale warning writer"}
        )
        save_state(state_path, warning_writer)

        state = load_state(state_path)
        assert _failed_tasks(state) == ["T1"]
        assert "max_turns_exceeded" in _warning_reasons(state)


def test_done_removes_only_that_task_failure_and_preserves_concurrent_updates() -> None:
    with _temp_state_path() as state_path:
        save_state(
            state_path,
            {
                "done": [],
                "failed": [
                    {"task": "T1", "reason": "build_failed"},
                    {"task": "T2", "reason": "test_failed"},
                ],
                "warnings": [{"task": "T0", "reason": "preexisting_warning"}],
            },
        )
        stale_done_writer = load_state(state_path)

        concurrent_failure_writer = load_state(state_path)
        record_task_failure_state(
            concurrent_failure_writer,
            state_path=state_path,
            task_id="T3",
            reason="policy_violation",
            detail="concurrent failure",
        )
        append_state_warning(
            state_path,
            {"task": "T4", "reason": "concurrent_warning"},
        )
        mark_state_task_done(state_path, "T5")

        mark_state_task_done(state_path, "T1", state=stale_done_writer)

        state = load_state(state_path)
        assert state["done"] == ["T1", "T5"]
        assert _failed_tasks(state) == ["T2", "T3"]
        assert set(_warning_reasons(state)) == {"preexisting_warning", "concurrent_warning"}


@pytest.mark.parametrize("contents", ["", "{not valid json"])
def test_corrupt_or_empty_state_recovers_with_warning_and_requested_mutation(contents) -> None:
    with _temp_state_path() as state_path:
        state_path.write_text(contents, encoding="utf-8")

        state: dict = {}
        record_task_failure_state(
            state,
            state_path=state_path,
            task_id="T9",
            reason="exception",
            detail="requested mutation survived recovery",
        )

        recovered = load_state(state_path)
        assert _failed_tasks(recovered) == ["T9"]
        assert "state_recovered" in _warning_reasons(recovered)


def test_duplicate_entries_are_not_amplified_across_repeated_saves() -> None:
    with _temp_state_path() as state_path:
        stale_writer = load_state(state_path)
        record_task_failure_state(
            stale_writer,
            task_id="T1",
            reason="build_failed",
            detail="same failure",
        )

        save_state(state_path, stale_writer)
        save_state(state_path, stale_writer)
        append_state_warning(
            state_path,
            {"task": "T2", "reason": "same_warning", "detail": "same warning"},
            state=stale_writer,
        )
        save_state(state_path, stale_writer)
        save_state(state_path, stale_writer)

        state = load_state(state_path)
        assert _failed_tasks(state) == ["T1"]
        assert _warning_reasons(state).count("same_warning") == 1
