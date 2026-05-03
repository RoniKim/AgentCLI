from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_runner.web import create_app


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_config(path: Path, repo: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "repo": repo.as_posix(),
        "profile": "enterprise",
        "execution_backend": "codex",
        "roles": ["PM", "Dev", "QA"],
        "iterations": 3,
        "prompts_dir": "prompts/agentcli",
        "goals_completion_level": "all",
        "telegram": {
            "enabled": True,
            "bot_token": "secret-token",
            "pairing_code": "PAIR-1234",
        },
    }
    for key, value in overrides.items():
        if key == "telegram" and isinstance(value, dict):
            payload.setdefault("telegram", {})
            assert isinstance(payload["telegram"], dict)
            payload["telegram"].update(value)
        else:
            payload[key] = value
    _write_json(path, payload)


def _relative_to(path: str | Path, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def _normalize_prompt_source(source: str, home: Path) -> str:
    source_path = Path(source)
    if source_path.is_absolute():
        return _relative_to(source_path, home)
    return source.replace("\\", "/")


@dataclass
class EndpointFixture:
    root: Path
    repo: Path
    home: Path
    config_path: Path
    prompts_dir: Path
    goals_path: Path
    run_dir: Path

    def create_client(self) -> TestClient:
        app = create_app(self.repo, web_dir=WEB_CONSOLE, config_path=str(self.config_path))
        return TestClient(app)


@pytest.fixture()
def endpoint_fixture(monkeypatch: pytest.MonkeyPatch) -> EndpointFixture:
    tmp_root = ROOT / ".test-scratch"
    tmp_root.mkdir(parents=True, exist_ok=True)
    root = tmp_root / f"web_endpoint_golden_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    repo = root / "repo"
    home = root / "home"
    repo.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENTCLI_HOME", str(home))

    config_path = home / "configs" / "agentcli.json"
    prompts_dir = home / "prompts" / "agentcli"
    goals_path = repo / ".doc" / "GOALS.md"
    run_dir = repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)

    _write_config(config_path, repo)
    fixture = EndpointFixture(
        root=root,
        repo=repo,
        home=home,
        config_path=config_path,
        prompts_dir=prompts_dir,
        goals_path=goals_path,
        run_dir=run_dir,
    )
    try:
        yield fixture
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_api_config_contract_exposes_metadata_redaction_backups_and_mutation_routes(endpoint_fixture: EndpointFixture) -> None:
    backup_path = endpoint_fixture.config_path.with_name("agentcli.20260426-120000.bak.json")
    _write(
        backup_path,
        '{\n  "repo": "/tmp/backup",\n  "iterations": 2\n}\n',
    )
    os.utime(backup_path, (1_714_145_200, 1_714_145_200))

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/config").json()

    backup = payload["backups"][0]
    normalized = {
        "path": _relative_to(payload["path"], endpoint_fixture.home),
        "source": payload["source"],
        "resolved_prompts_dir": _relative_to(payload["resolved_prompts_dir"], endpoint_fixture.home),
        "repo_value": _relative_to(payload["values"]["repo"], endpoint_fixture.root),
        "profile": payload["values"]["profile"],
        "telegram": {
            "bot_token": payload["values"]["telegram"]["bot_token"],
            "pairing_code": payload["values"]["telegram"]["pairing_code"],
        },
        "repo_schema": {
            "kind": payload["schema"]["repo"]["kind"],
            "label": payload["schema"]["repo"]["label"],
            "editable": payload["schema"]["repo"]["editable"],
            "restart": payload["schema"]["repo"]["restart"],
        },
        "bot_token_schema": {
            "kind": payload["schema"]["telegram.bot_token"]["kind"],
            "editable": payload["schema"]["telegram.bot_token"]["editable"],
            "redacted": payload["schema"]["telegram.bot_token"]["redacted"],
        },
        "redaction": {
            "placeholder": payload["redaction"]["placeholder"],
            "has_bot_token": "telegram.bot_token" in payload["redaction"]["paths"],
            "has_pairing_code": "telegram.pairing_code" in payload["redaction"]["paths"],
        },
        "backup": {
            "path": _relative_to(backup["path"], endpoint_fixture.home),
            "name": backup["name"],
            "size": backup["size"],
            "has_updated": bool(backup["updated"]),
            "summary_has_size": backup["summary"].endswith(f"{backup['size']} bytes"),
        },
        "meta": {
            "path": _relative_to(payload["meta"]["path"], endpoint_fixture.home),
            "resolved_prompts_dir": _relative_to(payload["meta"]["resolved_prompts_dir"], endpoint_fixture.home),
            "save_enabled": payload["meta"]["save_enabled"],
            "save_endpoint": payload["meta"]["save_endpoint"],
            "save_requires_opt_in": payload["meta"]["save_requires_opt_in"],
            "restore_enabled": payload["meta"]["restore_enabled"],
            "restore_endpoint": payload["meta"]["restore_endpoint"],
            "restore_requires_opt_in": payload["meta"]["restore_requires_opt_in"],
        },
    }

    assert normalized == {
        "path": "configs/agentcli.json",
        "source": "explicit",
        "resolved_prompts_dir": "prompts/agentcli",
        "repo_value": "repo",
        "profile": "enterprise",
        "telegram": {
            "bot_token": "[redacted]",
            "pairing_code": "[redacted]",
        },
        "repo_schema": {
            "kind": "text",
            "label": "Repository",
            "editable": True,
            "restart": True,
        },
        "bot_token_schema": {
            "kind": "text",
            "editable": True,
            "redacted": True,
        },
        "redaction": {
            "placeholder": "[redacted]",
            "has_bot_token": True,
            "has_pairing_code": True,
        },
        "backup": {
            "path": "configs/agentcli.20260426-120000.bak.json",
            "name": "agentcli.20260426-120000.bak.json",
            "size": 51,
            "has_updated": True,
            "summary_has_size": True,
        },
        "meta": {
            "path": "configs/agentcli.json",
            "resolved_prompts_dir": "prompts/agentcli",
            "save_enabled": False,
            "save_endpoint": "/api/config/save",
            "save_requires_opt_in": True,
            "restore_enabled": False,
            "restore_endpoint": "/api/config/restore",
            "restore_requires_opt_in": True,
        },
    }


def test_api_goals_contract_returns_raw_text_parsed_items_and_checkbox_state(endpoint_fixture: EndpointFixture) -> None:
    goals_text = """# Project Goals

## P0
- [x] Lock config payload contract
- [ ] Preserve prompt preview redaction

## P1
- [ ] Distinguish missing and malformed logs
"""
    _write(endpoint_fixture.goals_path, goals_text)

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/goals").json()

    normalized = {
        "path": _relative_to(payload["path"], endpoint_fixture.repo),
        "exists": payload["exists"],
        "completion_level": payload["completion_level"],
        "raw_text": payload["raw_text"],
        "items": {
            "p0": [
                {
                    "text": item["text"],
                    "checked": item["checked"],
                    "checkbox": item["checkbox"],
                    "line_number": item["line_number"],
                }
                for item in payload["items"]["p0"]
            ],
            "p1": [
                {
                    "text": item["text"],
                    "checked": item["checked"],
                    "checkbox": item["checkbox"],
                    "line_number": item["line_number"],
                }
                for item in payload["items"]["p1"]
            ],
        },
        "summary": {
            "p0_total": payload["summary"]["p0_total"],
            "p0_done": payload["summary"]["p0_done"],
            "p1_total": payload["summary"]["p1_total"],
            "p1_done": payload["summary"]["p1_done"],
            "total": payload["summary"]["total"],
            "done": payload["summary"]["done"],
            "unchecked": payload["summary"]["unchecked"],
            "warnings": payload["summary"]["warnings"],
        },
        "completion": {
            "has_goals": payload["completion"]["has_goals"],
            "project_complete": payload["completion"]["project_complete"],
            "valid": payload["completion"]["valid"],
            "missing_sections": payload["completion"]["missing_sections"],
        },
    }

    assert normalized == {
        "path": ".doc/GOALS.md",
        "exists": True,
        "completion_level": "all",
        "raw_text": goals_text,
        "items": {
            "p0": [
                {
                    "text": "Lock config payload contract",
                    "checked": True,
                    "checkbox": "[x]",
                    "line_number": 4,
                },
                {
                    "text": "Preserve prompt preview redaction",
                    "checked": False,
                    "checkbox": "[ ]",
                    "line_number": 5,
                },
            ],
            "p1": [
                {
                    "text": "Distinguish missing and malformed logs",
                    "checked": False,
                    "checkbox": "[ ]",
                    "line_number": 8,
                },
            ],
        },
        "summary": {
            "p0_total": 2,
            "p0_done": 1,
            "p1_total": 1,
            "p1_done": 0,
            "total": 3,
            "done": 1,
            "unchecked": 2,
            "warnings": 0,
        },
        "completion": {
            "has_goals": True,
            "project_complete": False,
            "valid": True,
            "missing_sections": [],
        },
    }


def test_api_prompts_contract_returns_profile_aware_inventory_with_redacted_previews(endpoint_fixture: EndpointFixture) -> None:
    prompt_body = """# Local PM Instructions

Profile: {profile}
Repo: {repo}
"""
    _write(endpoint_fixture.prompts_dir / "pm_instructions.md", prompt_body)

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/prompts").json()

    items = {item["id"]: item for item in payload["items"]}
    override = items["pm_instructions"]
    template = items["pm_bootstrap"]

    normalized = {
        "dir": _relative_to(payload["dir"], endpoint_fixture.home),
        "profiles": sorted({item["profile"] for item in payload["items"]}),
        "items_without_content": all("content" not in item for item in payload["items"]),
        "override": {
            "file": override["file"],
            "path": _relative_to(override["path"], endpoint_fixture.home),
            "source": _normalize_prompt_source(str(override["source"]), endpoint_fixture.home),
            "scope": override["scope"],
            "profile": override["profile"],
            "mode": override["mode"],
            "preview": override["preview"],
            "content_length": override["content_length"],
        },
        "template": {
            "file": template["file"],
            "path": _relative_to(template["path"], endpoint_fixture.home),
            "source": _normalize_prompt_source(str(template["source"]), endpoint_fixture.home),
            "scope": template["scope"],
            "profile": template["profile"],
            "mode": template["mode"],
            "preview": template["preview"],
            "has_content_length": template["content_length"] > 0,
        },
    }

    assert normalized == {
        "dir": "prompts/agentcli",
        "profiles": ["enterprise"],
        "items_without_content": True,
        "override": {
            "file": "pm_instructions.md",
            "path": "prompts/agentcli/pm_instructions.md",
            "source": "prompts/agentcli",
            "scope": "PM",
            "profile": "enterprise",
            "mode": "override",
            "preview": "[redacted]",
            "content_length": len(prompt_body),
        },
        "template": {
            "file": "pm_bootstrap_prompt.md",
            "path": "prompts/agentcli/pm_bootstrap_prompt.md",
            "source": "templates/agent_prompts",
            "scope": "PM",
            "profile": "enterprise",
            "mode": "template",
            "preview": "[redacted]",
            "has_content_length": True,
        },
    }


def test_api_logs_tail_contract_distinguishes_missing_empty_and_malformed_sources(endpoint_fixture: EndpointFixture) -> None:
    _write(endpoint_fixture.run_dir / "logs" / "error.log", "")
    _write(
        endpoint_fixture.run_dir / "logs" / "events.jsonl",
        "\n".join(
            [
                "not json at all",
                json.dumps(
                    {
                        "ts": "2026-04-26T12:00:20",
                        "seq": 3,
                        "level": "info",
                        "event": "task_end",
                        "stage": "Dev",
                        "task_id": "T-023",
                        "message": "task end",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
    )

    with endpoint_fixture.create_client() as client:
        missing = client.get("/api/logs/tail", params={"source": "run_log"}).json()
        empty = client.get("/api/logs/tail", params={"source": "error_log"}).json()
        malformed = client.get("/api/logs/tail", params={"source": "events_jsonl"}).json()

    def _shape(payload: dict[str, object]) -> dict[str, object]:
        source = payload["source"]
        assert isinstance(source, dict)
        return {
            "ok": payload["ok"],
            "state": payload["state"],
            "source_id": payload["source_id"],
            "selected_source_id": payload["selected_source_id"],
            "source_name": source["name"],
            "source_available": source["available"],
            "next_cursor": payload["next_cursor"],
            "malformed_lines": payload["malformed_lines"],
            "entry_lines": [entry["line_number"] for entry in payload["entries"]],
            "entry_messages": [entry["msg"] for entry in payload["entries"]],
        }

    assert _shape(missing) == {
        "ok": False,
        "state": "missing_file",
        "source_id": "run_log",
        "selected_source_id": "run_log",
        "source_name": "run.log",
        "source_available": False,
        "next_cursor": 0,
        "malformed_lines": 0,
        "entry_lines": [],
        "entry_messages": [],
    }
    assert _shape(empty) == {
        "ok": True,
        "state": "empty",
        "source_id": "error_log",
        "selected_source_id": "error_log",
        "source_name": "error.log",
        "source_available": True,
        "next_cursor": 0,
        "malformed_lines": 0,
        "entry_lines": [],
        "entry_messages": [],
    }
    assert _shape(malformed) == {
        "ok": True,
        "state": "malformed_line",
        "source_id": "events_jsonl",
        "selected_source_id": "events_jsonl",
        "source_name": "events.jsonl",
        "source_available": True,
        "next_cursor": 2,
        "malformed_lines": 1,
        "entry_lines": [2],
        "entry_messages": ["task end"],
    }
