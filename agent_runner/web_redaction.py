from __future__ import annotations

import ipaddress
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


SENSITIVE_CONFIG_TOKENS = {
    "api",
    "apikey",
    "api_key",
    "auth",
    "bearer",
    "bot",
    "chat",
    "client_secret",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "token",
    "webhook",
}
REDACTED_VALUE = "[redacted]"


def _path_text(value: Path | str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().as_posix()
    except Exception:
        return raw.replace("\\", "/")


def _host_is_loopback(bind_host: str) -> bool:
    host = str(bind_host or "").strip()
    if not host:
        return True
    if host.lower() == "localhost":
        return True
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _resolve_trusted_operator_gate_enabled() -> tuple[bool, str]:
    # Placeholder for a future authenticated or otherwise stronger operator gate.
    return False, "not-implemented"


def _is_sensitive_config_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if not normalized:
        return False
    parts = {part for part in normalized.split("_") if part}
    if "pairing" in parts and "code" in parts:
        return True
    return normalized in SENSITIVE_CONFIG_TOKENS or bool(parts & SENSITIVE_CONFIG_TOKENS)


def _redact_config(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_config_key(key):
        if isinstance(value, bool) or value in (None, ""):
            return value
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {str(item_key): _redact_config(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _web_redaction_active(bind_host: str) -> bool:
    return not _host_is_loopback(bind_host)


def _redact_web_text(value: Any) -> Any:
    if value in (None, "", False):
        return value
    return REDACTED_VALUE


def _web_redaction_meta(*fields: str) -> dict[str, Any]:
    cleaned = [str(field).strip() for field in fields if str(field).strip()]
    return {
        "active": True,
        "placeholder": REDACTED_VALUE,
        "fields": list(dict.fromkeys(cleaned)),
        "scope": "lan",
    }


def _web_apply_redaction(payload: Any, *, active: bool, redactor: Any | None = None) -> Any:
    if not active or redactor is None:
        return payload
    try:
        return redactor(payload)
    except Exception:
        return payload


def _redact_web_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(entry)
    for key in ("msg", "message", "raw", "text", "reason", "detail", "excerpt", "output", "content", "preview", "path", "trace", "stack"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    return redacted


def _redact_web_local_retention_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    path_keys = {
        "repo_root",
        "repoRoot",
        "artifact_path",
        "artifactPath",
        "path",
        "relative_path",
        "covered_by",
        "validation_artifact",
    }

    def redact_paths(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key in path_keys and item not in (None, "", False):
                    result[key] = REDACTED_VALUE
                else:
                    result[key] = redact_paths(item)
            return result
        if isinstance(value, list):
            return [redact_paths(item) for item in value]
        return value

    redacted = redact_paths(redacted)
    redacted["redaction"] = _web_redaction_meta(
        "repo_root",
        "artifact_path",
        "roots",
        "active_run_dirs",
        "protected_roots",
        "candidates.path",
        "candidates.relative_path",
        "candidates.pending_review_evidence.path",
        "candidates.pending_review_evidence.validation_artifact",
    )
    return redacted


def _redact_web_todo_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    for key in (
        "dir",
        "todo_dir",
        "todoDir",
        "pointer_path",
        "pointerPath",
        "active_path",
        "activePath",
        "active_relative_path",
        "activeRelativePath",
        "path",
        "backup_path",
        "backupPath",
    ):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    preview = redacted.get("preview")
    if isinstance(preview, dict):
        preview = deepcopy(preview)
        if preview.get("text") not in (None, "", False):
            preview["text"] = REDACTED_VALUE
        if preview.get("lines"):
            preview["lines"] = []
        redacted["preview"] = preview
    todo = redacted.get("todo")
    if isinstance(todo, dict):
        redacted["todo"] = _redact_web_todo_payload(todo)
    redacted["redaction"] = _web_redaction_meta(
        "dir",
        "pointer_path",
        "active_path",
        "active_relative_path",
        "preview.text",
        "preview.lines",
    )
    return redacted


def _redact_web_log_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    entries = redacted.get("entries")
    if isinstance(entries, list):
        redacted["entries"] = [_redact_web_log_entry(entry) if isinstance(entry, dict) else entry for entry in entries]
    for key in ("last_line", "lastLine"):
        last_line = redacted.get(key)
        if isinstance(last_line, dict):
            redacted[key] = _redact_web_log_entry(last_line)
    tail = redacted.get("tail")
    if tail not in (None, "", False):
        redacted["tail"] = REDACTED_VALUE
    files = redacted.get("files")
    if isinstance(files, dict):
        files_copy = deepcopy(files)
        redaction_fields: list[str] = []
        for key, value in list(files_copy.items()):
            if value not in (None, "", False):
                files_copy[key] = REDACTED_VALUE
                redaction_fields.append(f"files.{str(key).strip()}")
        redacted["files"] = files_copy
    else:
        redaction_fields = []
    for key in ("source_file", "source_path", "error"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    source = redacted.get("source")
    if isinstance(source, dict):
        source_copy = deepcopy(source)
        if source_copy.get("path") not in (None, "", False):
            source_copy["path"] = REDACTED_VALUE
        if source_copy.get("name") not in (None, "", False):
            source_copy["name"] = REDACTED_VALUE
        redacted["source"] = source_copy
    sources = redacted.get("sources")
    if isinstance(sources, list):
        redacted_sources: list[dict[str, Any] | Any] = []
        for item in sources:
            if not isinstance(item, dict):
                redacted_sources.append(item)
                continue
            item_copy = deepcopy(item)
            if item_copy.get("path") not in (None, "", False):
                item_copy["path"] = REDACTED_VALUE
            if item_copy.get("name") not in (None, "", False):
                item_copy["name"] = REDACTED_VALUE
            redacted_sources.append(item_copy)
        redacted["sources"] = redacted_sources
    redacted["redaction"] = _web_redaction_meta(
        "entries.msg",
        "entries.message",
        "entries.raw",
        "entries.text",
        "entries.reason",
        "entries.detail",
        "entries.excerpt",
        "entries.output",
        "entries.content",
        "entries.preview",
        "entries.path",
        "entries.trace",
        "entries.stack",
        "last_line.msg",
        "last_line.message",
        "last_line.raw",
        "last_line.text",
        "last_line.reason",
        "lastLine.msg",
        "lastLine.message",
        "lastLine.raw",
        "lastLine.text",
        "lastLine.reason",
        "tail",
        *redaction_fields,
        "source.path",
        "source.name",
        "sources.path",
        "sources.name",
        "source_file",
        "source_path",
        "error",
    )
    return redacted


def _redact_web_goal_warning(warning: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(warning)
    for key in ("line", "raw", "text", "excerpt", "message", "detail"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    return redacted


def _redact_web_goals_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    if redacted.get("raw_text") not in (None, "", False):
        redacted["raw_text"] = REDACTED_VALUE
    if redacted.get("rawText") not in (None, "", False):
        redacted["rawText"] = REDACTED_VALUE
    warnings = redacted.get("warnings")
    if isinstance(warnings, list):
        redacted["warnings"] = [_redact_web_goal_warning(warning) if isinstance(warning, dict) else warning for warning in warnings]
    redacted["redaction"] = _web_redaction_meta("raw_text", "rawText", "warnings.raw", "warnings.text", "warnings.excerpt")
    return redacted


def _redact_web_backlog_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    for key in (
        "prompt",
        "description",
        "skills_rationale",
        "skillsRationale",
        "recent_output",
        "recentOutput",
        "failure_detail",
        "failureDetail",
        "output",
        "output_excerpt",
        "outputExcerpt",
        "excerpt",
        "detail",
        "trace",
        "stack",
    ):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    failure = redacted.get("failure")
    if isinstance(failure, dict):
        failure_copy = deepcopy(failure)
        for key in ("detail", "message", "output", "excerpt", "trace", "stack"):
            if failure_copy.get(key) not in (None, "", False):
                failure_copy[key] = REDACTED_VALUE
        redacted["failure"] = failure_copy
    return redacted


def _redact_web_backlog_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    items = redacted.get("items")
    if isinstance(items, list):
        redacted["items"] = [_redact_web_backlog_item(item) if isinstance(item, dict) else item for item in items]
    redacted["redaction"] = _web_redaction_meta(
        "items.recent_output",
        "items.recentOutput",
        "items.output",
        "items.outputExcerpt",
        "items.excerpt",
        "items.detail",
        "items.trace",
        "items.stack",
        "items.failure.detail",
        "items.failure.message",
        "items.failure.output",
        "items.failure.excerpt",
        "items.failure.trace",
        "items.failure.stack",
        "items.failureDetail",
    )
    return redacted


def _redact_web_stage(stage: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(stage)
    changed = False
    for key in ("latestLogLine", "latest_log_line", "latestBackendEvent", "latest_backend_event", "recentOutput", "recent_output"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            changed = True
    if changed:
        redacted["redaction"] = _web_redaction_meta("latestLogLine", "latest_log_line", "latestBackendEvent", "latest_backend_event", "recentOutput", "recent_output")
    return redacted


def _redact_web_stages_payload(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_redact_web_stage(stage) if isinstance(stage, dict) else stage for stage in stages]


def _redact_web_prompt_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    for key in ("preview", "content"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    return redacted


def _redact_web_prompts_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    if redacted.get("dir") not in (None, "", False):
        redacted["dir"] = REDACTED_VALUE
    items = redacted.get("items")
    if isinstance(items, list):
        redacted["items"] = [_redact_web_prompt_item(item) if isinstance(item, dict) else item for item in items]
    redacted["redaction"] = _web_redaction_meta("dir", "items.preview", "items.content")
    return redacted


def _redact_web_notification_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    for key in ("text", "message", "detail", "task_title", "taskTitle"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    links = redacted.get("links")
    if isinstance(links, list):
        redacted["links"] = [
            {
                **link,
                "search": REDACTED_VALUE,
            }
            if isinstance(link, dict) and link.get("search") not in (None, "", False)
            else link
            for link in links
        ]
    redacted["redaction"] = _web_redaction_meta("text", "message", "detail", "task_title", "taskTitle", "links.search")
    return redacted


def _redact_web_notifications_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_redact_web_notification_item(item) if isinstance(item, dict) else item for item in items]


def _redact_web_history_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    redaction_fields: list[str] = []
    for key in ("lastCycle", "last_cycle"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    for key in ("runSummary", "run_summary", "lastRunSummary", "last_run_summary", "cycleChangeSummary", "cycle_change_summary", "operationsSummary", "operations_summary", "failedTasks", "failed_tasks"):
        summary = redacted.get(key)
        if isinstance(summary, dict):
            redacted[key] = _redact_web_history_summary(summary)
            redaction_fields.append(key)
    for key in ("qaValidationReport", "qa_validation_report", "finalRunReport", "final_run_report"):
        report = redacted.get(key)
        if isinstance(report, dict):
            redacted[key] = _redact_web_history_summary(report)
            redaction_fields.append(key)
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_history_summary(summary: Any) -> Any:
    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            redacted_value: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in {
                    "path",
                    "config_path",
                    "configPath",
                    "defaults_path",
                    "defaultsPath",
                    "recentOutput",
                    "recent_output",
                    "output",
                    "outputExcerpt",
                    "output_excerpt",
                    "excerpt",
                    "raw_text",
                    "rawText",
                    "content",
                    "text",
                }:
                    redacted_value[key] = REDACTED_VALUE if item not in (None, "", False) else item
                elif key_text in {"start_options", "startOptions"} and isinstance(item, dict):
                    redacted_value[key] = _redact_web_runner_start_options(item)
                else:
                    redacted_value[key] = _walk(item)
            return redacted_value
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(deepcopy(summary))


def _redact_web_history_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    items = redacted.get("items")
    if isinstance(items, list):
        redacted["items"] = [_redact_web_history_item(item) if isinstance(item, dict) else item for item in items]
    redacted["redaction"] = _web_redaction_meta(
        "items.lastCycle",
        "items.last_cycle",
        "items.runSummary",
        "items.run_summary",
        "items.lastRunSummary",
        "items.last_run_summary",
        "items.cycleChangeSummary",
        "items.cycle_change_summary",
    )
    return redacted


def _redact_web_pr_queue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            redacted_value: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in {
                    "sourceRepo",
                    "source_repo",
                    "worktreeDir",
                    "worktree_dir",
                    "packetPath",
                    "packet_path",
                    "queueRoot",
                    "queue_root",
                    "path",
                    "artifactPath",
                    "artifact_path",
                    "preview",
                    "detail",
                    "reason",
                    "summary",
                    "goal_text",
                    "goalText",
                    "validationDetail",
                    "validation_detail",
                    "output",
                    "message",
                    "title",
                    "qaNotes",
                    "qa_notes",
                }:
                    if key_text in {"qaNotes", "qa_notes"} and isinstance(item, list):
                        redacted_value[key] = [REDACTED_VALUE for entry in item if entry not in (None, "", False)]
                    elif isinstance(item, (dict, list)):
                        redacted_value[key] = _walk(item)
                    else:
                        redacted_value[key] = REDACTED_VALUE if item not in (None, "", False, []) else item
                elif key_text == "lines" and isinstance(item, list):
                    redacted_value[key] = [REDACTED_VALUE for line in item if line not in (None, "")]
                elif key_text in {"artifacts", "validation_artifacts", "diffArtifacts", "diff_artifacts"} and isinstance(item, list):
                    redacted_value[key] = [_walk(entry) for entry in item]
                elif key_text in {"changedFiles", "changed_files", "diffFiles", "diff_files"} and isinstance(item, list):
                    redacted_value[key] = [_walk(entry) for entry in item]
                elif key_text in {"blockingReasons", "blocking_reasons"} and isinstance(item, list):
                    redacted_value[key] = [_walk(entry) for entry in item]
                else:
                    redacted_value[key] = _walk(item)
            return redacted_value
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    redacted = _walk(deepcopy(payload))
    if isinstance(redacted, dict):
        redacted["redaction"] = _web_redaction_meta(
            "queueRoot",
            "queue_root",
            "items.sourceRepo",
            "items.source_repo",
            "items.worktreeDir",
            "items.worktree_dir",
            "items.packetPath",
            "items.packet_path",
            "items.qaNotes",
            "items.qa_notes",
            "items.goalTrace.goal_text",
            "items.goalTrace.goalText",
            "items.blockingReasons.message",
            "items.blockingReasons.detail",
            "items.blockingReasons.reason",
            "items.blockingReasons.summary",
            "items.dependencyDetails.title",
            "items.dependencyDetails.dependencies.title",
            "items.dependencyDetails.dependents.title",
            "items.changedFiles.path",
            "items.changedFiles.summary",
            "items.changedFiles.hunks.lines",
            "items.diffArtifacts.path",
            "items.diffArtifacts.preview",
            "items.validation.reason",
            "items.validation.detail",
            "items.validation.summary",
            "items.validation.records.message",
            "items.validation.records.detail",
            "items.validation.records.reason",
            "items.validation.records.summary",
            "items.validation.artifacts.path",
            "items.validation.artifacts.preview",
            "detail.sourceRepo",
            "detail.source_repo",
            "detail.worktreeDir",
            "detail.worktree_dir",
            "detail.packetPath",
            "detail.packet_path",
            "detail.qaNotes",
            "detail.qa_notes",
            "detail.goalTrace.goal_text",
            "detail.goalTrace.goalText",
            "detail.blockingReasons.message",
            "detail.blockingReasons.detail",
            "detail.blockingReasons.reason",
            "detail.blockingReasons.summary",
            "detail.dependencyDetails.title",
            "detail.dependencyDetails.dependencies.title",
            "detail.dependencyDetails.dependents.title",
            "detail.changedFiles.path",
            "detail.changedFiles.summary",
            "detail.changedFiles.hunks.lines",
            "detail.diffArtifacts.path",
            "detail.diffArtifacts.preview",
            "detail.validation.reason",
            "detail.validation.detail",
            "detail.validation.summary",
            "detail.validation.records.message",
            "detail.validation.records.detail",
            "detail.validation.records.reason",
            "detail.validation.records.summary",
            "detail.validation.artifacts.path",
            "detail.validation.artifacts.preview",
        )
    return redacted


def _redact_web_runner_start_options(start_options: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(start_options)
    redaction = dict(redacted.get("redaction") or {})
    redaction["active"] = True
    redaction["placeholder"] = REDACTED_VALUE
    paths_value = redaction.get("paths")
    redaction_paths = []
    if isinstance(paths_value, list):
        redaction_paths = list(dict.fromkeys([str(path) for path in paths_value if str(path).strip()]))
    for path in ("repo", "path", "defaults_path", "values.config_path", "defaults.config_path", "values.run_dir", "defaults.run_dir"):
        if path not in redaction_paths:
            redaction_paths.append(path)
    redaction["paths"] = redaction_paths
    redacted["redaction"] = redaction
    redacted["redacted"] = True
    for key in ("repo", "path", "defaults_path"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    for key in ("values", "defaults"):
        section = redacted.get(key)
        if isinstance(section, dict):
            section_copy = deepcopy(section)
            if section_copy.get("config_path") not in (None, "", False):
                section_copy["config_path"] = REDACTED_VALUE
            if section_copy.get("run_dir") not in (None, "", False):
                section_copy["run_dir"] = REDACTED_VALUE
            redacted[key] = section_copy
    argv_preview = redacted.get("argv_preview")
    if isinstance(argv_preview, list):
        preview = list(argv_preview)
        for idx, token in enumerate(preview):
            if token in {"--repo", "--config", "--run-dir"} and idx + 1 < len(preview):
                preview[idx + 1] = REDACTED_VALUE
        redacted["argv_preview"] = preview
    return redacted


def _redact_web_runner_status_payload(status: dict[str, Any], *, redact_start_options: bool = False) -> dict[str, Any]:
    redacted = deepcopy(status)
    redaction_fields: list[str] = []
    for key in ("config_path", "configPath", "repo", "repoPath", "run_dir", "runDir", "worktree_dir", "worktreeDir"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    start_options = redacted.get("start_options")
    if redact_start_options and isinstance(start_options, dict):
        redacted_start_options = _redact_web_runner_start_options(start_options)
        redacted["start_options"] = redacted_start_options
        redacted["startOptions"] = redacted_start_options
        redaction_fields.extend(
            [
                "start_options.repo",
                "start_options.path",
                "start_options.defaults_path",
                "start_options.values.config_path",
                "start_options.defaults.config_path",
                "start_options.values.run_dir",
                "start_options.defaults.run_dir",
                "start_options.argv_preview",
            ]
        )
    current_event = redacted.get("current_event")
    if isinstance(current_event, dict):
        redacted_current_event = _redact_web_runner_result(current_event)
        redacted["current_event"] = redacted_current_event
        redacted["currentEvent"] = redacted_current_event
        redaction_fields.append("current_event")
    history = redacted.get("history")
    if isinstance(history, list):
        redacted_history = [_redact_web_runner_result(item) if isinstance(item, dict) else item for item in history]
        redacted["history"] = redacted_history
        redacted["event_history"] = redacted_history
        redacted["eventHistory"] = redacted_history
        redaction_fields.append("history")
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_runner_control(control: dict[str, Any], *, redact_start_options: bool = False) -> dict[str, Any]:
    redacted = deepcopy(control)
    redaction_fields: list[str] = []
    status = redacted.get("status")
    if isinstance(status, dict):
        status_copy = _redact_web_runner_status_payload(status, redact_start_options=redact_start_options)
        redacted["status"] = status_copy
        for key in ("config_path", "configPath", "repo", "repoPath", "run_dir", "runDir", "worktree_dir", "worktreeDir"):
            if status.get(key) not in (None, "", False):
                redaction_fields.append(f"status.{key}")
            if redact_start_options and isinstance(status.get("start_options"), dict):
                redaction_fields.extend(
                    [
                        "status.start_options.repo",
                        "status.start_options.path",
                        "status.start_options.defaults_path",
                        "status.start_options.values.config_path",
                        "status.start_options.defaults.config_path",
                        "status.start_options.values.run_dir",
                        "status.start_options.defaults.run_dir",
                        "status.start_options.argv_preview",
                    ]
                )
    current_event = redacted.get("current_event")
    if isinstance(current_event, dict):
        redacted_current_event = _redact_web_runner_result(current_event)
        redacted["current_event"] = redacted_current_event
        redacted["currentEvent"] = redacted_current_event
        redaction_fields.append("current_event")
    history = redacted.get("history")
    if isinstance(history, list):
        redacted_history = [_redact_web_runner_result(item) if isinstance(item, dict) else item for item in history]
        redacted["history"] = redacted_history
        redacted["event_history"] = redacted_history
        redacted["eventHistory"] = redacted_history
        redaction_fields.append("history")
    start_options = redacted.get("start_options")
    if redact_start_options and isinstance(start_options, dict):
        redacted_start_options = _redact_web_runner_start_options(start_options)
        redacted["start_options"] = redacted_start_options
        redacted["startOptions"] = redacted_start_options
        redaction_fields.extend(
            [
                "start_options.repo",
                "start_options.path",
                "start_options.defaults_path",
                "start_options.values.config_path",
                "start_options.defaults.config_path",
                "start_options.values.run_dir",
                "start_options.defaults.run_dir",
                "start_options.argv_preview",
            ]
        )
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_runner_result(result: dict[str, Any]) -> dict[str, Any]:
    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            redacted_value: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in {"config_path", "configPath"}:
                    redacted_value[key] = REDACTED_VALUE if item not in (None, "", False) else item
                elif key_text in {"repo", "repoPath", "run_dir", "runDir", "worktree_dir", "worktreeDir"}:
                    redacted_value[key] = REDACTED_VALUE if item not in (None, "", False) else item
                elif key_text in {"start_options", "startOptions"} and isinstance(item, dict):
                    redacted_value[key] = _redact_web_runner_start_options(item)
                else:
                    redacted_value[key] = _walk(item)
            return redacted_value
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(deepcopy(result))


def _redact_web_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    redaction_fields: list[str] = []
    for key in ("path", "resolved_prompts_dir"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    meta = redacted.get("meta")
    if isinstance(meta, dict):
        meta_copy = deepcopy(meta)
        for key in ("path", "resolved_prompts_dir"):
            if meta_copy.get(key) not in (None, "", False):
                meta_copy[key] = REDACTED_VALUE
                redaction_fields.append(f"meta.{key}")
        redacted["meta"] = meta_copy
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_config_contract(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    redaction_fields: list[str] = []
    for key in ("path", "resolved_prompts_dir"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    meta = redacted.get("meta")
    if isinstance(meta, dict):
        meta_copy = deepcopy(meta)
        for key in ("path", "resolved_prompts_dir"):
            if meta_copy.get(key) not in (None, "", False):
                meta_copy[key] = REDACTED_VALUE
                redaction_fields.append(f"meta.{key}")
        redacted["meta"] = meta_copy
    if redaction_fields:
        redaction = dict(redacted.get("redaction") or {})
        redaction["active"] = True
        redaction["placeholder"] = REDACTED_VALUE
        redaction["scope"] = "lan"
        redaction_fields_existing: list[str] = []
        for field in list(redaction.get("fields") or []):
            field_text = str(field).strip()
            if field_text:
                redaction_fields_existing.append(field_text)
        redaction["fields"] = list(dict.fromkeys(redaction_fields_existing + redaction_fields))
        redacted["redaction"] = redaction
    return redacted


def _lan_safety_blocks_mutations(bind_host: str) -> bool:
    gate_enabled, _ = _resolve_trusted_operator_gate_enabled()
    return bool(_web_redaction_active(bind_host) and not gate_enabled)


__all__ = [
    "REDACTED_VALUE",
    "SENSITIVE_CONFIG_TOKENS",
    "_is_sensitive_config_key",
    "_lan_safety_blocks_mutations",
    "_redact_config",
    "_redact_web_backlog_item",
    "_redact_web_backlog_payload",
    "_redact_web_config_contract",
    "_redact_web_config_payload",
    "_redact_web_goal_warning",
    "_redact_web_goals_payload",
    "_redact_web_history_item",
    "_redact_web_history_payload",
    "_redact_web_history_summary",
    "_redact_web_log_entry",
    "_redact_web_log_payload",
    "_redact_web_notification_item",
    "_redact_web_notifications_payload",
    "_redact_web_pr_queue_payload",
    "_redact_web_prompt_item",
    "_redact_web_prompts_payload",
    "_redact_web_runner_control",
    "_redact_web_runner_result",
    "_redact_web_runner_start_options",
    "_redact_web_runner_status_payload",
    "_redact_web_stage",
    "_redact_web_stages_payload",
    "_redact_web_text",
    "_web_apply_redaction",
    "_web_redaction_active",
    "_web_redaction_meta",
]
