from __future__ import annotations

import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runner.experience import (
    EXPERIENCE_SCHEMA_VERSION,
    experience_db_path,
    initialize_experience_db,
    query_completed_task_experiences,
    record_completed_task_experience,
)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    base = Path.home() / ".codex" / "memories"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"agentcli-experience-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def test_initialize_experience_db_creates_required_tables(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    paths = initialize_experience_db(repo)

    assert paths.root_dir == (repo / ".AgentCLI" / "experience").resolve()
    assert paths.db_path.exists()
    assert paths.schema_version_path.exists()
    assert paths.schema_version_path.read_text(encoding="utf-8") == f"{EXPERIENCE_SCHEMA_VERSION}\n"
    assert {
        "schema_migrations",
        "runs",
        "task_experiences",
        "validation_experiences",
        "file_patterns",
        "lessons",
    }.issubset(_table_names(paths.db_path))


def test_initialize_experience_db_is_idempotent_and_preserves_rows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    paths = initialize_experience_db(repo)
    with sqlite3.connect(str(paths.db_path)) as conn:
        conn.execute(
            "INSERT INTO runs(run_id, started_at, backend, source_head, stop_reason, summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("run-1", "2026-05-03T00:00:00+00:00", "codex", "abc123", "completed", "first run"),
        )
        conn.commit()

    second_paths = initialize_experience_db(repo)

    assert second_paths == paths
    with sqlite3.connect(str(paths.db_path)) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        migration_count = conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 1").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert run_count == 1
    assert migration_count == 1
    assert user_version == EXPERIENCE_SCHEMA_VERSION


def test_initialize_experience_db_rejects_paths_outside_agent_work_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside_dir = tmp_path / "outside"

    with pytest.raises(ValueError, match="Experience path escapes repo work dir"):
        initialize_experience_db(repo, experience_dir=outside_dir)

    assert not (repo / ".AgentCLI").exists()
    assert not outside_dir.exists()


def test_initialize_experience_db_schema_excludes_raw_prompt_log_and_diff_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    paths = initialize_experience_db(repo)

    for table_name in ("runs", "task_experiences", "validation_experiences", "file_patterns", "lessons"):
        columns = _table_columns(paths.db_path, table_name)
        assert "raw_prompt" not in columns
        assert "raw_log" not in columns
        assert "raw_diff" not in columns
        assert "prompt" not in columns
        assert "diff" not in columns


def test_task_experience_records_use_repo_local_experience_db_after_schema_init(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    paths = initialize_experience_db(repo)
    record_completed_task_experience(
        repo,
        run_id="run-1",
        task_id="T1",
        title="Persist repo-local task experience",
        status="done",
        task_status="completed",
        validation_status="validation_passed",
        goal_trace=[{"goal_ref": "P0-U"}],
        changed_files=["agent_runner/experience.py"],
        branch_ref="task/T1",
        head_ref="abc123",
        base_ref="main",
        validation_artifacts=[],
        pr_packet_ids=["pr-run-1-t1"],
    )

    assert experience_db_path(repo) == paths.db_path
    assert query_completed_task_experiences(repo, run_id="run-1", task_id="T1")[0]["pr_packet_ids"] == [
        "pr-run-1-t1"
    ]
    with sqlite3.connect(str(paths.db_path)) as conn:
        columns = _table_columns(paths.db_path, "task_experiences")
        row_count = conn.execute("SELECT COUNT(*) FROM task_experiences").fetchone()[0]

    assert "id" in columns
    assert "branch_ref" in columns
    assert row_count == 1
