from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime_contract import PIPELINE_STAGE_ORDER


def _web_payload_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _web_payload_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def build_live_state_payload(
    web: Any,
    controller_status: dict[str, Any] | None,
    *,
    progress: dict[str, Any] | None = None,
    active_run: dict[str, Any] | None = None,
    controller_available: bool = False,
) -> dict[str, Any]:
    _pick_text = web._pick_text
    _live_state_entry = web._live_state_entry
    normalize_stop_progress_payload = web.normalize_stop_progress_payload
    summarize_stop_progress_liveness = web.summarize_stop_progress_liveness

    status = controller_status if isinstance(controller_status, dict) else {}
    progress_data = progress if isinstance(progress, dict) else {}
    active = active_run if isinstance(active_run, dict) else {}
    status_reason = str(status.get("reason") or "").strip()
    controller_live_available = bool(controller_available and status and not status_reason.startswith("status_error:"))
    controller_running = bool(status.get("running")) if controller_live_available else False

    stop_progress = status.get("stop_progress")
    if not isinstance(stop_progress, dict):
        stop_progress = {}
    else:
        stop_progress = normalize_stop_progress_payload(stop_progress)
    stop_progress_liveness = summarize_stop_progress_liveness(stop_progress)

    run_status = _pick_text(
        progress_data.get("run_status"),
        progress_data.get("runStatus"),
        active.get("status"),
        active.get("runStatus"),
        active.get("executionStatus"),
        active.get("execution_status"),
    ).strip().lower()
    backend_alive_states = {"running", "stopping"}
    backend_stopped_states = {
        "stopped",
        "completed",
        "complete",
        "success",
        "done",
        "failed",
        "error",
        "aborted",
        "cancelled",
        "canceled",
        "interrupted",
        "timeout",
    }
    backend_available = controller_live_available and bool(
        run_status
        or active.get("status")
        or active.get("executionStatus")
        or active.get("execution_status")
        or stop_progress
    )
    backend_status = "unavailable"
    if backend_available:
        if run_status in backend_alive_states:
            backend_status = "alive"
        elif run_status in backend_stopped_states or bool(stop_progress_liveness["phase"]):
            backend_status = "stopped"
        else:
            backend_status = "idle"
    backend_alive = backend_status == "alive"

    tracked_child_processes = stop_progress_liveness["tracked_child_processes"]
    tracked_child_pids = stop_progress_liveness["tracked_child_pids"]
    tracked_available = controller_live_available and bool(stop_progress)
    tracked_alive_count = int(stop_progress_liveness["tracked_alive_count"])
    tracked_count = int(stop_progress_liveness["tracked_count"])
    tracked_alive = tracked_available and tracked_alive_count > 0
    tracked_status = "unavailable"
    if tracked_available:
        if tracked_alive:
            tracked_status = "alive"
        elif tracked_count > 0:
            tracked_status = "stopped"
        else:
            tracked_status = "idle"

    artifact_phase = stop_progress_liveness["artifact_phase"]
    artifact_available = controller_live_available and bool(stop_progress)
    artifact_flushing = artifact_available and bool(stop_progress_liveness["artifact_flushing"])
    artifact_status = "unavailable" if not artifact_available else ("flushing" if artifact_flushing else "idle")

    runner_process = _live_state_entry(
        "runner_process",
        available=controller_live_available,
        status="alive" if controller_running else "stopped",
        source="controller.status.running" if controller_live_available else "unavailable",
        alive=controller_running if controller_live_available else None,
    )
    task_backend = _live_state_entry(
        "task_backend",
        available=backend_available,
        status=backend_status,
        source="progress.run_status" if run_status else "active_run.status" if backend_available else "unavailable",
        alive=backend_alive if backend_available else None,
    )
    tracked_children = _live_state_entry(
        "tracked_children",
        available=tracked_available,
        status=tracked_status,
        source=(
            "stop_progress.tracked_child_processes"
            if tracked_child_processes
            else "stop_progress.tracked_child_pids" if tracked_child_pids else "stop_progress"
        ),
        alive=tracked_alive if tracked_available else None,
        count=tracked_count if tracked_available else None,
        alive_count=tracked_alive_count if tracked_available else None,
    )
    artifact_writer = _live_state_entry(
        "artifact_writer",
        available=artifact_available,
        status=artifact_status,
        source=(
            "stop_progress.current_phase.phase"
            if artifact_phase
            else "stop_progress.last_artifact_signal" if artifact_available else "unavailable"
        ),
        flushing=artifact_flushing if artifact_available else None,
        phase=artifact_phase if artifact_available else "",
    )
    live_state = {
        "available": bool(controller_live_available or backend_available or tracked_available or artifact_available),
        "source": "controller.status + progress + stop_progress" if controller_live_available else "unavailable",
        "runner_process": runner_process,
        "runnerProcess": runner_process,
        "task_backend": task_backend,
        "taskBackend": task_backend,
        "tracked_children": tracked_children,
        "trackedChildren": tracked_children,
        "artifact_writer": artifact_writer,
        "artifactWriter": artifact_writer,
    }
    live_state["items"] = [
        live_state["runner_process"],
        live_state["task_backend"],
        live_state["tracked_children"],
        live_state["artifact_writer"],
    ]
    return live_state


def build_stage_payload(
    web: Any,
    repo: Path,
    active_run: dict[str, Any],
    progress: dict[str, Any],
    config: dict[str, Any],
    *,
    run_dir: Path | None = None,
    run_summary: dict[str, Any] | None = None,
    last_run_summary: dict[str, Any] | None = None,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    _cycle_events = web._cycle_events
    _task_runtime_index = web._task_runtime_index
    _normalize_stage_name = web._normalize_stage_name
    _pick_text = web._pick_text
    _coerce_optional_int = web._coerce_optional_int
    _pick_value = web._pick_value
    _iso_to_ms = web._iso_to_ms
    _event_message = web._event_message
    _coerce_optional_ms = web._coerce_optional_ms
    _coerce_optional_float = web._coerce_optional_float
    _task_output_signal = web._task_output_signal
    _stage_output_stall_threshold_seconds = web._stage_output_stall_threshold_seconds
    _task_output_excerpt = web._task_output_excerpt
    _normalize_lifecycle_status = web._normalize_lifecycle_status

    controller_data = controller_status if isinstance(controller_status, dict) else {}
    run_summary = run_summary if isinstance(run_summary, dict) else {}
    last_run_summary = last_run_summary if isinstance(last_run_summary, dict) else {}
    events = list(events) if isinstance(events, list) else (_cycle_events(run_dir) if run_dir is not None else [])
    task_runtime = _task_runtime_index(events)
    snapshot_now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    stage_titles = {
        "PM": "Backlog planning",
        "PL": "Backlog refinement",
        "Security": "Security",
        "Dev": "Implementation",
        "QA": "Verification",
        "Reporter": "Close-out reporting",
    }
    stage_model_defaults = {
        "PM": str(config.get("pm_model") or "gpt-5.5"),
        "PL": "",
        "Security": "",
        "Dev": str(config.get("dev_model") or "gpt-5.4-mini"),
        "QA": str(config.get("qa_model") or "gpt-5.5"),
        "Reporter": str(config.get("reporter_model") or "gpt-5.4-mini"),
    }
    summary_stage_order: list[str] = []
    observed_builtin_stage_names: set[str] = set()
    observed_custom_stage_names: list[str] = []

    def _observe_stage_name(stage_name: str, *, summary_order: bool = False) -> None:
        if not stage_name:
            return
        if summary_order and stage_name not in summary_stage_order:
            summary_stage_order.append(stage_name)
        if stage_name in PIPELINE_STAGE_ORDER:
            observed_builtin_stage_names.add(stage_name)
        elif stage_name not in observed_custom_stage_names:
            observed_custom_stage_names.append(stage_name)

    def _ordered_stage_names() -> list[str]:
        ordered = list(summary_stage_order)
        for stage_name in PIPELINE_STAGE_ORDER:
            if stage_name in observed_builtin_stage_names and stage_name not in ordered:
                ordered.append(stage_name)
        for stage_name in observed_custom_stage_names:
            if stage_name not in ordered:
                ordered.append(stage_name)
        return ordered

    active_status = str(active_run.get("status") or progress.get("run_status") or "idle").strip().lower()
    current_stage = _normalize_stage_name(
        _pick_text(
            controller_data.get("stage"),
            controller_data.get("current_stage"),
            active_run.get("stage"),
            progress.get("current_stage"),
        )
    )
    if not current_stage and active_status == "running" and _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        active_run.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    ):
        current_stage = "Dev"
    current_task_id = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        active_run.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    )
    current_task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
        active_run.get("taskTitle"),
        progress.get("current_task_title"),
    )
    current_attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            active_run.get("attempt"),
            progress.get("attempt"),
            progress.get("current_attempt"),
            last_run_summary.get("attempt"),
            run_summary.get("attempt"),
        )
    )
    cycles = run_summary.get("cycles") if isinstance(run_summary.get("cycles"), list) else []
    cycle_entries = [entry for entry in cycles if isinstance(entry, dict)]
    latest_summary_cycle: int | None = None
    for entry in cycle_entries:
        cycle_value = _coerce_optional_int(entry.get("cycle"))
        if cycle_value is not None and (latest_summary_cycle is None or cycle_value > latest_summary_cycle):
            latest_summary_cycle = cycle_value

    current_cycle = _coerce_optional_int(
        _pick_value(
            controller_data.get("cycle"),
            controller_data.get("current_cycle"),
            active_run.get("iteration"),
            progress.get("iterations"),
            last_run_summary.get("cycle"),
            latest_summary_cycle,
        )
    )
    event_cycles = [
        cycle_value
        for cycle_value in (_coerce_optional_int(event.get("cycle")) for event in events)
        if cycle_value is not None
    ]
    latest_event_cycle = max(event_cycles) if event_cycles else None
    if active_status == "running":
        target_cycle = current_cycle or latest_event_cycle or latest_summary_cycle
    else:
        target_cycle = latest_summary_cycle or latest_event_cycle or current_cycle
    target_cycle = _coerce_optional_int(target_cycle)

    target_cycle_entry: dict[str, Any] = {}
    if target_cycle is not None:
        for entry in reversed(cycle_entries):
            if _coerce_optional_int(entry.get("cycle")) == target_cycle:
                target_cycle_entry = entry
                break

    stage_summary_map: dict[str, dict[str, Any]] = {}
    for raw_stage in target_cycle_entry.get("stages") if isinstance(target_cycle_entry.get("stages"), list) else []:
        if not isinstance(raw_stage, dict):
            continue
        stage_name = _normalize_stage_name(
            _pick_value(raw_stage.get("name"), raw_stage.get("id"), raw_stage.get("label"))
        )
        if not stage_name:
            continue
        stage_summary_map[stage_name] = raw_stage
        _observe_stage_name(stage_name, summary_order=True)

    running_stage_name = current_stage if active_status == "running" else ""
    relevant_events = [
        event
        for event in events
        if target_cycle is None or _coerce_optional_int(event.get("cycle")) in {None, target_cycle}
    ]

    stage_event_map: dict[str, dict[str, Any]] = {}
    for event in relevant_events:
        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        stage_name = _normalize_stage_name(_pick_value(event.get("stage"), payload.get("stage")))
        if not stage_name and (event_type.startswith("pm_") or event_type.startswith("pm_stage_")):
            stage_name = "PM"
        elif not stage_name and (event_type.startswith("qa_") or event_type.startswith("qa_stage_")):
            stage_name = "QA"
        elif not stage_name and event_type.startswith("security_"):
            stage_name = "Security"
        elif not stage_name and event_type.startswith("reporter_"):
            stage_name = "Reporter"
        if not stage_name:
            continue
        if event_type not in {
            "pm_start",
            "pm_end",
            "pm_stage_start",
            "pm_stage_end",
            "qa_start",
            "qa_end",
            "qa_stage_start",
            "qa_stage_end",
            "security_start",
            "security_end",
            "security_skipped",
            "stage_event",
        }:
            continue
        _observe_stage_name(stage_name)
        entry = stage_event_map.setdefault(
            stage_name,
            {
                "cycle": None,
                "status": "",
                "startedAt": None,
                "endedAt": None,
                "rc": None,
                "reason": "",
                "model": "",
                "taskId": "",
                "taskTitle": "",
                "attempt": None,
                "step": None,
                "lastMessage": "",
                "lastEvent": "",
                "lastEventAt": None,
            },
        )
        ts = _iso_to_ms(event.get("ts"))
        if ts is not None:
            started_at = _coerce_optional_int(entry.get("startedAt"))
            ended_at = _coerce_optional_int(entry.get("endedAt"))
            if event_type.endswith("start") and (started_at is None or ts < started_at):
                entry["startedAt"] = ts
            if event_type.endswith("end") and (ended_at is None or ts > ended_at):
                entry["endedAt"] = ts
            current_last_event_at = _coerce_optional_int(entry.get("lastEventAt"))
            entry["lastEventAt"] = ts if current_last_event_at is None else max(current_last_event_at, ts)
        cycle_value = _coerce_optional_int(_pick_value(event.get("cycle"), payload.get("cycle")))
        if cycle_value is not None and entry["cycle"] is None:
            entry["cycle"] = cycle_value
        reason = _pick_text(event.get("reason"), payload.get("reason"), payload.get("detail"))
        inner_event = _pick_text(payload.get("event")) if event_type == "stage_event" else ""
        if event_type == "stage_event":
            reason = _pick_text(reason, inner_event)
        if reason:
            entry["reason"] = reason
        model = _pick_text(event.get("model"), payload.get("model"))
        if model and not entry["model"]:
            entry["model"] = model
        task_id = _pick_text(event.get("task_id"), event.get("task"), payload.get("task_id"), payload.get("task"))
        if task_id and not entry["taskId"]:
            entry["taskId"] = task_id
        task_title = _pick_text(
            event.get("task_title"),
            event.get("taskTitle"),
            event.get("title"),
            payload.get("task_title"),
            payload.get("taskTitle"),
            payload.get("title"),
        )
        if task_title and not entry["taskTitle"]:
            entry["taskTitle"] = task_title
        attempt = _coerce_optional_int(_pick_value(event.get("attempt"), payload.get("attempt")))
        if attempt is not None:
            entry["attempt"] = attempt
        step = _coerce_optional_int(_pick_value(event.get("step"), payload.get("step")))
        if step is not None and entry["step"] is None:
            entry["step"] = step
        if event_type.endswith("start"):
            entry["status"] = "running"
        elif event_type.endswith("end"):
            rc = _coerce_optional_int(_pick_value(event.get("rc"), payload.get("rc")))
            if rc is not None:
                entry["rc"] = rc
                entry["status"] = "done" if rc == 0 else "failed"
            elif reason:
                entry["status"] = _normalize_lifecycle_status(reason, reason=reason, default="done")
            elif not entry["status"]:
                entry["status"] = "done"
        elif event_type.endswith("skipped"):
            entry["status"] = "skipped"
        elif event_type == "stage_event":
            if inner_event in {"error", "quota_exhausted"} or reason in {"error", "quota_exhausted"}:
                entry["status"] = "failed"
            elif not entry["status"]:
                entry["status"] = "running"
        message = _pick_text(payload.get("detail"), payload.get("reason"), reason, inner_event, _event_message(event))
        if message:
            entry["lastMessage"] = message
        event_label = inner_event or _event_message(event)
        if event_label:
            entry["lastEvent"] = event_label

    def _runtime_key(entry: dict[str, Any]) -> tuple[int, int, int, int, int]:
        return (
            _coerce_optional_int(entry.get("cycle")) or -1,
            _coerce_optional_int(entry.get("attempt")) or -1,
            _coerce_optional_int(entry.get("step")) or -1,
            _coerce_optional_int(entry.get("startedAt")) or -1,
            _coerce_optional_int(entry.get("endedAt")) or -1,
        )

    cycle_task_runtimes = [
        entry
        for entry in task_runtime.values()
        if target_cycle is None or _coerce_optional_int(entry.get("cycle")) in {None, target_cycle}
    ]
    latest_task_runtime: dict[str, Any] = {}
    if cycle_task_runtimes:
        if current_task_id and current_task_id in task_runtime and (
            target_cycle is None or _coerce_optional_int(task_runtime[current_task_id].get("cycle")) in {None, target_cycle}
        ):
            latest_task_runtime = task_runtime[current_task_id]
        else:
            latest_task_runtime = max(cycle_task_runtimes, key=_runtime_key)

    if latest_task_runtime:
        _observe_stage_name("Dev")
    if running_stage_name:
        _observe_stage_name(running_stage_name)

    stage_names = _ordered_stage_names()
    records: list[dict[str, Any]] = []
    for stage_name in stage_names:
        summary = stage_summary_map.get(stage_name, {})
        stage_runtime = latest_task_runtime if stage_name == "Dev" else stage_event_map.get(stage_name, {})

        summary_status = _pick_text(summary.get("status"))
        summary_reason = _pick_text(summary.get("reason"), summary.get("message"))
        summary_cycle = _coerce_optional_int(summary.get("cycle"))
        summary_step = _coerce_optional_int(summary.get("step"))
        runtime_cycle = _coerce_optional_int(stage_runtime.get("cycle"))
        cycle_value = summary_cycle if summary_cycle is not None else runtime_cycle
        if cycle_value is None:
            cycle_value = current_cycle if stage_name == running_stage_name and active_status == "running" else target_cycle

        started_at = _coerce_optional_ms(
            _pick_value(
                summary.get("startedAt"),
                summary.get("started_at"),
                stage_runtime.get("startedAt"),
                stage_runtime.get("started_at"),
            )
        )
        ended_at = _coerce_optional_ms(
            _pick_value(
                summary.get("endedAt"),
                summary.get("ended_at"),
                stage_runtime.get("endedAt"),
                stage_runtime.get("ended_at"),
            )
        )
        rc = _coerce_optional_int(_pick_value(summary.get("rc"), stage_runtime.get("rc")))
        reason = _pick_text(summary_reason, stage_runtime.get("reason"))
        task_id = _pick_text(summary.get("taskId"), summary.get("task_id"), stage_runtime.get("taskId"))
        task_title = _pick_text(summary.get("taskTitle"), summary.get("task_title"), stage_runtime.get("taskTitle"))
        step = _coerce_optional_int(_pick_value(summary_step, stage_runtime.get("step")))
        attempt = _coerce_optional_int(
            _pick_value(
                summary.get("attempt"),
                summary.get("currentAttempt"),
                stage_runtime.get("attempt"),
                current_attempt if stage_name in {"Dev", "PM", "QA"} or stage_name == running_stage_name else None,
            )
        )
        model = _pick_text(
            summary.get("model"),
            stage_runtime.get("model"),
            stage_model_defaults.get(stage_name, ""),
        )

        if stage_name == "Dev":
            if not task_id:
                task_id = current_task_id or _pick_text(stage_runtime.get("taskId"))
            if not task_title:
                task_title = current_task_title or _pick_text(stage_runtime.get("taskTitle"))
        elif stage_name == running_stage_name and active_status == "running":
            if not task_id:
                task_id = current_task_id
            if not task_title:
                task_title = current_task_title

        running = stage_name == running_stage_name and active_status == "running"
        if running:
            started_at = started_at or _coerce_optional_int(active_run.get("startedAt")) or _coerce_optional_int(
                controller_data.get("startedAt")
            )
            ended_at = None
            if attempt is None:
                attempt = current_attempt
            if not task_id:
                task_id = current_task_id or _pick_text(active_run.get("task"))
            if not task_title:
                task_title = current_task_title or _pick_text(active_run.get("taskTitle"))

        duration_sec = _coerce_optional_float(
            _pick_value(
                summary.get("durationSec"),
                summary.get("duration_seconds"),
                stage_runtime.get("durationSec"),
                stage_runtime.get("duration_seconds"),
            )
        )
        if duration_sec is None and started_at is not None and ended_at is not None and ended_at >= started_at:
            duration_sec = round((ended_at - started_at) / 1000.0, 3)
        if duration_sec is None and running:
            duration_sec = _coerce_optional_float(
                _pick_value(active_run.get("elapsedSec"), controller_data.get("elapsedSec"), controller_data.get("elapsed_seconds"))
            )

        output_reason = reason or summary_reason or _pick_text(stage_runtime.get("reason"))
        latest_log_signal = _task_output_signal(
            run_dir,
            stage_name=stage_name,
            cycle=cycle_value,
            step=step,
            task_id=task_id or current_task_id,
            attempt=attempt,
            reason=output_reason,
            include_summary_artifacts=False,
        )
        latest_log_line = _pick_text(latest_log_signal.get("text"))
        latest_log_line_mtime = _coerce_optional_int(latest_log_signal.get("mtimeMs"))
        latest_backend_event = _pick_text(
            stage_runtime.get("lastEvent"),
            stage_runtime.get("lastMessage"),
            stage_runtime.get("reason"),
            output_reason,
            summary_reason,
        )
        latest_backend_event_at = _coerce_optional_int(stage_runtime.get("lastEventAt"))
        elapsed_sec = duration_sec
        if running:
            if started_at is not None:
                elapsed_sec = round(max(0, snapshot_now_ms - started_at) / 1000.0, 3)
            elif elapsed_sec is None:
                elapsed_sec = _coerce_optional_float(
                    _pick_value(
                        stage_runtime.get("elapsedSec"),
                        active_run.get("elapsedSec"),
                        controller_data.get("elapsedSec"),
                        controller_data.get("elapsed_seconds"),
                    )
                )
        elif elapsed_sec is None and started_at is not None and ended_at is not None and ended_at >= started_at:
            elapsed_sec = round((ended_at - started_at) / 1000.0, 3)
        latest_signal_ms = max(
            [
                value
                for value in [latest_log_line_mtime, latest_backend_event_at, started_at if running else None]
                if value is not None
            ],
            default=None,
        )
        output_stalled = False
        no_output_minutes = None
        if running and latest_signal_ms is not None:
            stalled_threshold_ms = _stage_output_stall_threshold_seconds(config) * 1000
            age_ms = max(0, snapshot_now_ms - latest_signal_ms)
            if age_ms >= stalled_threshold_ms:
                output_stalled = True
                no_output_minutes = max(1, int(age_ms // 60000))
        recent_output = _pick_text(summary.get("recentOutput"), summary.get("recent_output"))
        if not recent_output:
            recent_output = _task_output_excerpt(
                run_dir,
                stage_name=stage_name,
                cycle=cycle_value,
                step=step,
                task_id=task_id or current_task_id,
                attempt=attempt,
                reason=output_reason,
                fallback_text=_pick_text(stage_runtime.get("lastMessage"), stage_runtime.get("lastEvent"), output_reason),
            )

        status = _normalize_lifecycle_status(
            summary_status or stage_runtime.get("status"),
            rc=rc,
            reason=reason,
            running=running,
            has_activity=bool(summary or stage_runtime or task_id or task_title or recent_output),
            default="pending",
        )

        if (
            not summary
            and not stage_runtime
            and not running
            and not recent_output
            and not task_id
            and not task_title
            and not reason
            and status == "pending"
        ):
            continue

        if running:
            status = "running"

        records.append(
            {
                "id": stage_name,
                "label": stage_name,
                "title": task_title or stage_titles.get(stage_name, stage_name),
                "status": status,
                "cycle": cycle_value,
                "startedAt": started_at,
                "endedAt": ended_at,
                "durationSec": duration_sec,
                "model": model,
                "taskId": task_id,
                "taskTitle": task_title,
                "attempt": attempt,
                "step": step,
                "recentOutput": recent_output,
                "elapsedSec": elapsed_sec,
                "latestLogLine": latest_log_line,
                "latestBackendEvent": latest_backend_event,
                "outputStalled": output_stalled,
                "noOutputMinutes": no_output_minutes,
                "reason": reason,
                "rc": rc,
            }
        )

    return records


def build_history_item(
    web: Any,
    repo: Path,
    run_dir: Path,
    *,
    branch: str,
    completion_level: str = "all",
) -> dict[str, Any]:
    load_state = web.load_state
    _load_tasks = web._load_tasks
    load_backlog_task_ids = web.load_backlog_task_ids
    count_state_task_ids = web.count_state_task_ids
    _build_goals_payload = web._build_goals_payload
    _safe_json = web._safe_json
    _pick_text = web._pick_text
    _completion_status_payload = web._completion_status_payload
    _coerce_optional_int = web._coerce_optional_int
    _pick_value = web._pick_value
    _project_completion_status = web._project_completion_status
    _normalize_execution_status = web._normalize_execution_status
    _normalize_run_status = web._normalize_run_status
    _epoch_ms = web._epoch_ms
    _latest_path_mtime_ms = web._latest_path_mtime_ms
    _history_worktree_outcome = web._history_worktree_outcome
    _build_metrics_payload = web._build_metrics_payload
    _tail_text = web._tail_text

    state = load_state(run_dir / "STATE.json")
    backlog = _load_tasks(run_dir)
    backlog_task_ids = load_backlog_task_ids(run_dir / "BACKLOG.json")
    state_counts = count_state_task_ids(state, backlog_task_ids)
    goals = _build_goals_payload(repo, completion_level=completion_level)
    run_summary = _safe_json(run_dir / "run_summary.json", {})
    last_summary = _safe_json(run_dir / "last_run_summary.json", {})

    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    final_reason = _pick_text(final.get("reason"), last_summary.get("reason"), last_summary.get("stop_reason"))
    shutdown_reason = _pick_text(last_summary.get("stop_reason"), final_reason)
    if not final_reason:
        final_reason = shutdown_reason
    completion = _completion_status_payload(run_dir, final_reason=final_reason)
    final_reason = completion["final_reason"]

    tasks_done = state_counts["done"]

    tasks_total = _coerce_optional_int(_pick_value(last_summary.get("total_tasks"), last_summary.get("tasks_total")))
    if tasks_total is None:
        tasks_total = len(backlog)

    tasks_failed = state_counts["failed"]

    tasks_skipped = _coerce_optional_int(_pick_value(last_summary.get("skipped"), last_summary.get("tasks_skipped")))
    if tasks_skipped is None:
        tasks_skipped = 0

    cycle_count = len(run_summary.get("cycles") or [])
    project_completion = _project_completion_status(
        goals,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
    )

    rc = _coerce_optional_int(final.get("rc"))
    if rc is None:
        rc = _coerce_optional_int(last_summary.get("rc"))
    stop_exists = (run_dir / "STOP").exists()
    if not shutdown_reason and stop_exists:
        shutdown_reason = "stop_file"
    if not final_reason and stop_exists:
        final_reason = shutdown_reason
    execution_status = _normalize_execution_status(
        "",
        running=False,
        exit_code=rc,
        final_reason=shutdown_reason,
        stop_file_exists=stop_exists,
        has_run_dir=True,
    )
    status = _normalize_run_status(
        "",
        running=False,
        exit_code=rc,
        final_reason=shutdown_reason,
        stop_file_exists=stop_exists,
        has_run_dir=True,
        project_complete=project_completion["project_complete"],
    )

    started_at = _epoch_ms(run_dir.stat().st_ctime)
    artifact_updated_at = _latest_path_mtime_ms(run_dir)
    ended_at = artifact_updated_at or _epoch_ms(run_dir.stat().st_mtime)
    duration_sec = _coerce_optional_int(
        _pick_value(
            last_summary.get("duration_seconds"),
            last_summary.get("durationSec"),
            run_summary.get("duration_seconds"),
            run_summary.get("durationSec"),
        )
    )
    if duration_sec is None:
        duration_sec = max(0, int((ended_at - started_at) / 1000)) if started_at and ended_at else 0
    if duration_sec == 0:
        duration_sec = max(0, cycle_count * 60)

    branch_value = _pick_text(run_summary.get("branch"), last_summary.get("branch"), branch, "HEAD")
    worktree_outcome = _history_worktree_outcome(run_dir)
    metrics = _build_metrics_payload(run_dir, {}, controller_status=None)
    tokens = metrics.get("tokens") if isinstance(metrics.get("tokens"), dict) else {}
    quota = metrics.get("quota") if isinstance(metrics.get("quota"), dict) else {}
    tokens_available = bool(metrics.get("tokens_available") or metrics.get("tokensAvailable"))
    quota_available = bool(metrics.get("quota_available") or metrics.get("quotaAvailable"))
    qa_validation_report = _safe_json(run_dir / "QA_VALIDATION_REPORT.json", {})
    final_run_report = _safe_json(run_dir / "FINAL_RUN_REPORT.json", {})
    cycle_change_summary = _safe_json(run_dir / "cycle_change_summary.json", {})
    operations_summary = _safe_json(run_dir / "OPERATIONS_SUMMARY.json", {})
    web_history_snapshot = _safe_json(run_dir / "WEB_HISTORY_SNAPSHOT.json", {})
    qa_validation_report = qa_validation_report if isinstance(qa_validation_report, dict) else {}
    final_run_report = final_run_report if isinstance(final_run_report, dict) else {}
    cycle_change_summary = cycle_change_summary if isinstance(cycle_change_summary, dict) else {}
    operations_summary = operations_summary if isinstance(operations_summary, dict) else {}
    web_history_snapshot = web_history_snapshot if isinstance(web_history_snapshot, dict) else {}
    report_summary = _pick_text(final_run_report.get("summary"), "")
    report_status = _pick_text(final_run_report.get("status"), "")
    qa_report_status = _pick_text(qa_validation_report.get("status"), "")
    failed_tasks = cycle_change_summary.get("failed_tasks") if isinstance(cycle_change_summary.get("failed_tasks"), dict) else {}

    last_cycle = _tail_text(run_dir / "cycle_summary.log", 1).strip()
    return {
        "id": run_dir.name,
        "startedAt": started_at,
        "endedAt": ended_at,
        "status": status,
        "executionStatus": execution_status,
        "execution_status": execution_status,
        "projectComplete": project_completion["project_complete"],
        "project_complete": project_completion["project_complete"],
        "projectStatus": project_completion["project_status"],
        "project_status": project_completion["project_status"],
        "completionStatus": completion["completionStatus"],
        "completion_status": completion["completion_status"],
        "completionReason": completion["completionReason"],
        "completion_reason": completion["completion_reason"],
        "goalsComplete": project_completion["goals_complete"],
        "goals_complete": project_completion["goals_complete"],
        "backlogComplete": project_completion["backlog_complete"],
        "backlog_complete": project_completion["backlog_complete"],
        "tasksDone": tasks_done,
        "tasksTotal": tasks_total,
        "tasksFailed": tasks_failed,
        "tasksSkipped": tasks_skipped,
        "state_counts": state_counts,
        "taskCounts": {
            "done": tasks_done,
            "failed": tasks_failed,
            "skipped": tasks_skipped,
            "total": tasks_total,
            "cycles": cycle_count,
        },
        "branch": branch_value,
        "durationSec": duration_sec,
        "finalReason": final_reason,
        "shutdownReason": shutdown_reason,
        "stopReason": shutdown_reason,
        "runDir": run_dir.as_posix(),
        "freshnessTimestamp": ended_at,
        "freshness_timestamp": ended_at,
        "freshnessSource": "artifact_mtime" if artifact_updated_at else "run_dir_mtime",
        "freshness_source": "artifact_mtime" if artifact_updated_at else "run_dir_mtime",
        "lastCycle": last_cycle,
        "runSummary": run_summary,
        "lastRunSummary": last_summary,
        "metrics": metrics,
        "tokens24h": list(metrics.get("tokens24h") or []),
        "tokensAvailable": tokens_available,
        "tokens_available": tokens_available,
        "tokens": {
            "in": tokens.get("in"),
            "out": tokens.get("out"),
            "available": tokens_available,
        },
        "quotaAvailable": quota_available,
        "quota_available": quota_available,
        "quotaWindow": metrics.get("quotaWindow") or metrics.get("quota_window") or quota.get("window"),
        "quota_window": metrics.get("quota_window") or metrics.get("quotaWindow") or quota.get("window"),
        "quotaUsed": metrics.get("quotaUsed") if metrics.get("quotaUsed") is not None else metrics.get("quota_used"),
        "quota_used": metrics.get("quota_used") if metrics.get("quota_used") is not None else metrics.get("quotaUsed"),
        "quota": quota,
        "worktreeOutcome": worktree_outcome,
        "qaValidationReport": qa_validation_report,
        "qa_validation_report": qa_validation_report,
        "finalRunReport": final_run_report,
        "final_run_report": final_run_report,
        "cycleChangeSummary": cycle_change_summary,
        "cycle_change_summary": cycle_change_summary,
        "operationsSummary": operations_summary,
        "operations_summary": operations_summary,
        "webHistorySnapshot": web_history_snapshot,
        "web_history_snapshot": web_history_snapshot,
        "failedTasks": failed_tasks,
        "failed_tasks": failed_tasks,
        "reportSummary": report_summary,
        "reportStatus": report_status,
        "qaValidationReportStatus": qa_report_status,
        "qa_validation_report_status": qa_report_status,
        "reportArtifacts": {
            "qaValidationJson": (run_dir / "QA_VALIDATION_REPORT.json").as_posix(),
            "qaValidationMarkdown": (run_dir / "QA_VALIDATION_REPORT.md").as_posix(),
            "finalRunJson": (run_dir / "FINAL_RUN_REPORT.json").as_posix(),
            "finalRunMarkdown": (run_dir / "FINAL_RUN_REPORT.md").as_posix(),
            "cycleChangeSummaryJson": (run_dir / "cycle_change_summary.json").as_posix(),
            "cycleChangeSummaryMarkdown": (run_dir / "cycle_change_summary.md").as_posix(),
            "operationsSummaryJson": (run_dir / "OPERATIONS_SUMMARY.json").as_posix(),
            "operationsSummaryMarkdown": (run_dir / "OPERATIONS_SUMMARY.md").as_posix(),
            "webHistorySnapshotJson": (run_dir / "WEB_HISTORY_SNAPSHOT.json").as_posix(),
            "workSummaryMarkdown": (run_dir / "WORK_SUMMARY.md").as_posix(),
            "webReportExportJson": (run_dir / "WEB_REPORT_EXPORT.json").as_posix(),
            "webReportExportMarkdown": (run_dir / "WEB_REPORT_EXPORT.md").as_posix(),
            "failedTasksJson": (run_dir / "failed_tasks.json").as_posix(),
            "failedTasksMarkdown": (run_dir / "failed_tasks.md").as_posix(),
            "shutdownReport": (run_dir / "SHUTDOWN_REPORT.md").as_posix(),
        },
    }


def build_history_payload(
    web: Any,
    repo: Path,
    run_dirs: list[Path],
    *,
    branch: str,
    completion_level: str = "all",
) -> dict[str, Any]:
    build_history_item = web._history_item

    items = [build_history_item(repo, run_dir, branch=branch, completion_level=completion_level) for run_dir in run_dirs]
    items.sort(key=lambda item: int(item.get("startedAt") or 0), reverse=True)
    successes = len([item for item in items if item["status"] == "success"])
    failures = len([item for item in items if item["status"] == "failed"])
    stopped = len([item for item in items if item["status"] == "stopped"])
    total_tasks = sum(int(item.get("tasksTotal") or 0) for item in items)
    done_tasks = sum(int(item.get("tasksDone") or 0) for item in items)
    failed_tasks = sum(int(item.get("tasksFailed") or 0) for item in items)
    skipped_tasks = sum(int(item.get("tasksSkipped") or 0) for item in items)
    return {
        "items": items,
        "summary": {
            "runs": len(items),
            "successes": successes,
            "failures": failures,
            "stopped": stopped,
            "tasksDone": done_tasks,
            "tasksTotal": total_tasks,
            "tasksFailed": failed_tasks,
            "tasksSkipped": skipped_tasks,
        },
    }


def build_metrics_payload(
    web: Any,
    run_dir: Path | None,
    progress: dict[str, Any],
    controller_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _cycle_events = web._cycle_events
    _coerce_optional_int = web._coerce_optional_int
    _coerce_optional_float = web._coerce_optional_float
    _pick_value = web._pick_value
    _quota_payload = web._quota_payload
    _quota_payload_from_source = web._quota_payload_from_source
    _pick_text = web._pick_text

    controller_data = controller_status if isinstance(controller_status, dict) else {}
    events = _cycle_events(run_dir)
    cycle_end_events = [event for event in events if str(event.get("event") or event.get("type") or "").strip().lower() == "cycle_end"]
    tokens24h: list[int] = []
    success24h: list[int] = []
    budget: list[float] = []
    last_tokens = {"in": None, "out": None}
    last_stage = ""
    quota = _quota_payload("", None)
    budget_used: float | None = None
    tokens_available = False
    budget_available = False

    for event in cycle_end_events:
        tokens = event.get("tokens") if isinstance(event.get("tokens"), dict) else {}
        total = tokens.get("_total") if isinstance(tokens.get("_total"), dict) else {}
        total_tokens = _coerce_optional_int(total.get("total"))
        if total_tokens is not None:
            tokens24h.append(total_tokens)
            tokens_available = True
        success24h.append(1 if int(event.get("rc") or 0) == 0 else 0)
        event_budget = _coerce_optional_float(_pick_value(event.get("budget"), event.get("budget_used"), event.get("budgetUsed")))
        if event_budget is not None:
            budget.append(round(max(0.0, min(1.0, event_budget)), 3))
            budget_available = True
            budget_used = max(budget_used or 0.0, event_budget)
        input_tokens = _coerce_optional_int(total.get("input"))
        output_tokens = _coerce_optional_int(total.get("output"))
        if input_tokens is not None or output_tokens is not None:
            last_tokens = {
                "in": input_tokens,
                "out": output_tokens,
            }
            tokens_available = True
        last_stage = str(event.get("stage") or event.get("name") or "").strip() or last_stage
        event_quota = _quota_payload_from_source(event)
        if event_quota.get("available"):
            quota = event_quota
    controller_tokens = controller_data.get("tokens") if isinstance(controller_data.get("tokens"), dict) else {}
    input_tokens = _coerce_optional_int(controller_tokens.get("in"))
    output_tokens = _coerce_optional_int(controller_tokens.get("out"))
    if input_tokens is not None or output_tokens is not None:
        last_tokens = {
            "in": input_tokens,
            "out": output_tokens,
        }
        tokens_available = True

    controller_quota = _quota_payload_from_source(controller_data)
    if controller_quota.get("available"):
        quota = controller_quota

    controller_budget = _coerce_optional_float(_pick_value(controller_data.get("budget_used"), controller_data.get("budgetUsed")))
    if controller_budget is not None:
        budget_used = controller_budget
        budget_available = True
        if not budget:
            budget.append(round(max(0.0, min(1.0, controller_budget)), 3))
    last_stage = _pick_text(controller_data.get("last_stage"), controller_data.get("stage"), last_stage)
    quota_available = bool(quota.get("available"))

    return {
        "tokens24h": tokens24h,
        "success24h": success24h,
        "budget": budget,
        "tokens": last_tokens,
        "tokens_available": tokens_available,
        "budget_available": budget_available,
        "quota_available": quota_available,
        "quotaAvailable": quota_available,
        "quota_window": quota.get("window"),
        "quotaWindow": quota.get("window"),
        "last_stage": last_stage,
        "quota_used": quota.get("used"),
        "quotaUsed": quota.get("used"),
        "budget_used": budget_used,
        "quota": quota,
    }


def build_progress_payload(
    web: Any,
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    branch: str,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    load_state = web.load_state
    _pick_text = web._pick_text
    _load_backlog_payload = web._load_backlog_payload
    load_backlog_task_ids = web.load_backlog_task_ids
    count_state_task_ids = web.count_state_task_ids
    resolve_goals_completion_level = web.resolve_goals_completion_level
    _build_goals_payload = web._build_goals_payload
    _project_completion_status = web._project_completion_status
    _safe_json = web._safe_json
    _completion_status_payload = web._completion_status_payload
    _coerce_optional_int = web._coerce_optional_int
    _normalize_execution_status = web._normalize_execution_status
    _normalize_run_status = web._normalize_run_status
    _coerce_optional_float = web._coerce_optional_float
    _pick_value = web._pick_value

    _ = branch
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    state = load_state(run_dir / "STATE.json") if run_dir else {"done": [], "failed": [], "warnings": []}
    backlog = _load_backlog_payload(
        run_dir,
        state,
        current_task_id=_pick_text(
            controller_data.get("current_task_id"),
            controller_data.get("task_id"),
            controller_data.get("task"),
        ),
        events=events,
    )
    backlog_task_ids = load_backlog_task_ids(run_dir / "BACKLOG.json") if run_dir else set()
    state_counts = count_state_task_ids(state, backlog_task_ids)
    completion_level = resolve_goals_completion_level(config.get("goals_completion_level"))
    goals = _build_goals_payload(repo, completion_level=completion_level)
    backlog_items = backlog["items"]
    tasks_total = len(backlog_items)
    tasks_done = state_counts["done"]
    tasks_failed = state_counts["failed"]
    project_completion = _project_completion_status(
        goals,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
    )
    run_summary = _safe_json(run_dir / "run_summary.json", {}) if run_dir else {}
    last_run_summary = _safe_json(run_dir / "last_run_summary.json", {}) if run_dir else {}
    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    final_reason = _pick_text(
        controller_data.get("final_reason"),
        controller_data.get("reason"),
        final.get("reason") if isinstance(final, dict) else "",
        last_run_summary.get("stop_reason"),
    )
    completion = _completion_status_payload(run_dir, final_reason=final_reason)
    final_reason = completion["final_reason"]
    final_rc = _coerce_optional_int(controller_data.get("exit_code"))
    if final_rc is None:
        final_rc = _coerce_optional_int(final.get("rc") if isinstance(final, dict) else None)
    if final_rc is None:
        final_rc = _coerce_optional_int(last_run_summary.get("rc") if isinstance(last_run_summary, dict) else None)
    stop_file_exists = bool(run_dir and (run_dir / "STOP").exists())
    execution_status = _normalize_execution_status(
        _pick_text(
            controller_data.get("run_status"),
            controller_data.get("status"),
            final.get("status") if isinstance(final, dict) else "",
            last_run_summary.get("status"),
        ),
        running=bool(controller_data.get("running")),
        exit_code=final_rc,
        final_reason=final_reason,
        stop_file_exists=stop_file_exists,
        has_run_dir=bool(run_dir),
    )
    run_status = _normalize_run_status(
        _pick_text(
            controller_data.get("run_status"),
            controller_data.get("status"),
            final.get("status") if isinstance(final, dict) else "",
            last_run_summary.get("status"),
        ),
        running=bool(controller_data.get("running")),
        exit_code=final_rc,
        final_reason=final_reason,
        stop_file_exists=stop_file_exists,
        has_run_dir=bool(run_dir),
        project_complete=project_completion["project_complete"],
    )
    current_task = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        backlog["selected_id"],
    )
    if not current_task:
        current_task = backlog["selected_id"] if backlog["selected_id"] else ""
    current_task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
    )
    if not current_task_title and current_task:
        current_task_title = next((item["title"] for item in backlog_items if item["id"] == current_task), "")
    progress_value = _coerce_optional_float(
        _pick_value(
            controller_data.get("progress"),
            controller_data.get("progress_ratio"),
            controller_data.get("progressValue"),
            run_summary.get("progress"),
        )
    )
    progress_available = progress_value is not None
    attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            run_summary.get("attempt"),
            last_run_summary.get("attempt"),
        )
    )
    worktree_mode = _pick_text(controller_data.get("worktree_mode"), controller_data.get("worktreeMode"))

    return {
        "latest_run_dir": run_dir.as_posix() if run_dir else None,
        "run_status": run_status,
        "tasks_done": tasks_done,
        "tasks_total": tasks_total,
        "tasks_failed": tasks_failed,
        "state_counts": state_counts,
        "progress": round(progress_value, 3) if progress_value is not None else None,
        "progress_available": progress_available,
        "execution_status": execution_status,
        "executionStatus": execution_status,
        "completion_status": completion["completion_status"],
        "completionStatus": completion["completionStatus"],
        "completion_reason": completion["completion_reason"],
        "completionReason": completion["completionReason"],
        "project_complete": project_completion["project_complete"],
        "projectComplete": project_completion["project_complete"],
        "project_status": project_completion["project_status"],
        "projectStatus": project_completion["project_status"],
        "goals_complete": project_completion["goals_complete"],
        "goalsComplete": project_completion["goals_complete"],
        "backlog_complete": project_completion["backlog_complete"],
        "backlogComplete": project_completion["backlog_complete"],
        "current_task_id": current_task,
        "current_task_title": current_task_title,
        "attempt": attempt,
        "worktree_mode": worktree_mode,
        "iterations": len(run_summary.get("cycles") or []),
        "goals": goals,
        "backlog": backlog,
        "state": state,
        "final_reason": final_reason,
        "final_rc": final_rc,
    }


def build_worktree_payload(web: Any, repo: Path, run_dir: Path | None, branch: str) -> dict[str, Any]:
    _repo_root = web._repo_root
    find_pending_worktree_merge = web.find_pending_worktree_merge
    read_pending_worktree_merge = web.read_pending_worktree_merge
    _safe_json = web._safe_json
    _worktree_default_payload = web._worktree_default_payload
    worktree_resolution_actions = web.worktree_resolution_actions
    _worktree_pending_is_stale = web._worktree_pending_is_stale
    _worktree_status_payload = web._worktree_status_payload
    _worktree_status_artifacts = web._worktree_status_artifacts
    _worktree_select_artifact = web._worktree_select_artifact
    WORKTREE_FINALIZED_HISTORY_STATUSES = web.WORKTREE_FINALIZED_HISTORY_STATUSES
    summarize_worktree_diff = web.summarize_worktree_diff
    git_show_toplevel = web.git_show_toplevel
    summarize_worktree_preflight = web.summarize_worktree_preflight

    repo_root = _repo_root(repo)
    run_dir_value = run_dir.as_posix() if run_dir else ""
    source_branch = branch or "HEAD"

    def _artifact_mtime_ns(item: tuple[str, Path]) -> int:
        try:
            return item[1].stat().st_mtime_ns
        except OSError:
            return 0

    def _attach_recent_artifacts(payload: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        recent = [dict(item) for item in artifacts]
        payload["historicalArtifacts"] = recent
        payload["historical_artifacts"] = recent
        payload["recentArtifacts"] = recent
        payload["recent_artifacts"] = recent
        payload["current"] = True
        payload["historical"] = False
        payload["isHistorical"] = False
        payload["mutatingActionsEnabled"] = bool(
            payload.get("status") in {"pending", "pending review"}
            and payload.get("cleanupState") == "pending"
            and payload.get("reviewRequired")
        )
        return payload

    def _finalized_artifact_payload(artifact_status: str, artifact_path: Path) -> dict[str, Any] | None:
        if artifact_status not in WORKTREE_FINALIZED_HISTORY_STATUSES:
            return None
        payload = _safe_json(artifact_path, {})
        if not isinstance(payload, dict):
            payload = {}
        artifact = _worktree_status_payload(
            repo_root,
            run_dir,
            branch,
            status=artifact_status,
            artifact_path=artifact_path,
            payload=payload,
            pending_path=None,
        )
        artifact["historicalArtifacts"] = []
        artifact["historical_artifacts"] = []
        artifact["recentArtifacts"] = []
        artifact["recent_artifacts"] = []
        artifact["current"] = False
        artifact["historical"] = True
        artifact["isHistorical"] = True
        artifact["mutatingActionsEnabled"] = False
        return artifact

    def _recent_finalized_artifacts(candidates: list[tuple[str, Path]]) -> list[dict[str, Any]]:
        recent: list[dict[str, Any]] = []
        seen: set[str] = set()
        for artifact_status, artifact_path in sorted(candidates, key=_artifact_mtime_ns, reverse=True):
            artifact = _finalized_artifact_payload(artifact_status, artifact_path)
            if artifact is None:
                continue
            key = artifact_path.resolve().as_posix() if artifact_path.exists() else artifact_path.as_posix()
            if key in seen:
                continue
            seen.add(key)
            recent.append(artifact)
            if len(recent) >= 12:
                break
        return recent

    artifact_candidates = [
        (artifact_status, artifact_path)
        for artifact_status, artifact_path in _worktree_status_artifacts(repo_root, run_dir)
        if artifact_status != "pending" and artifact_path.exists()
    ]
    recent_finalized_artifacts = _recent_finalized_artifacts(artifact_candidates)

    pending_path = find_pending_worktree_merge(repo_root, run_dir)
    if pending_path is not None:
        try:
            raw_payload = read_pending_worktree_merge(pending_path)
            if not isinstance(raw_payload, dict):
                raise TypeError("Pending merge payload must be a JSON object.")
        except Exception as ex:
            error_message = f"Pending worktree merge file is malformed: {str(ex).strip() or ex.__class__.__name__}"
            payload = {}
            if pending_path.exists():
                try:
                    payload = _safe_json(pending_path, {})
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
            invalid = _worktree_default_payload(repo_root, run_dir, branch)
            invalid.update(
                {
                    "status": "error",
                    "reviewRequired": True,
                    "reviewRequiredMessage": error_message,
                    "sourceRepo": str(payload.get("source_repo") or payload.get("sourceRepo") or invalid["sourceRepo"]).strip()
                    or invalid["sourceRepo"],
                    "sourceBranch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "branch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "baseRef": str(payload.get("base_ref") or payload.get("baseRef") or "").strip(),
                    "headRef": str(payload.get("head_ref") or payload.get("headRef") or "").strip(),
                    "worktreeDir": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "worktree": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "patchPath": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "patch": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "pendingFile": pending_path.as_posix(),
                    "statusFile": pending_path.as_posix(),
                    "cleanupPath": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "cleanupMessage": "Cleanup state is unavailable until the marker is repaired.",
                    "cleanupState": "none",
                    "summary": "Pending worktree merge file is malformed.",
                    "risk": "Fix or delete the pending merge file before applying any source-repo change.",
                    "changedFiles": [],
                    "changed_files": [],
                    "preflight": {},
                    "applyCheck": {},
                    "sourceRepoState": str(payload.get("source_repo_state") or payload.get("sourceRepoState") or ""),
                    "source_repo_state": str(payload.get("source_repo_state") or payload.get("sourceRepoState") or ""),
                    "sourceHead": str(payload.get("head_ref") or payload.get("headRef") or ""),
                    "source_head": str(payload.get("head_ref") or payload.get("headRef") or ""),
                    "expectedBaseRef": str(payload.get("base_ref") or payload.get("baseRef") or ""),
                    "expected_base_ref": str(payload.get("base_ref") or payload.get("baseRef") or ""),
                    "patchHash": str(payload.get("patch_hash") or payload.get("patchHash") or ""),
                    "patch_hash": str(payload.get("patch_hash") or payload.get("patchHash") or ""),
                    "pendingMarkerPath": pending_path.as_posix(),
                    "pending_marker_path": pending_path.as_posix(),
                    "resolutionActions": worktree_resolution_actions(
                        "stale_pending_marker",
                        pending_paths=[pending_path.as_posix()],
                        artifact_path=pending_path.as_posix(),
                    ),
                    "resolution_actions": worktree_resolution_actions(
                        "stale_pending_marker",
                        pending_paths=[pending_path.as_posix()],
                        artifact_path=pending_path.as_posix(),
                    ),
                    "runDir": str(payload.get("run_dir") or payload.get("runDir") or run_dir_value or "").strip(),
                    "runnerRc": 0,
                    "lastRc": 0,
                }
            )
            return _attach_recent_artifacts(invalid, recent_finalized_artifacts)

        payload = raw_payload
        stale_reason = _worktree_pending_is_stale(payload, pending_path)
        if stale_reason:
            invalid = _worktree_default_payload(repo_root, run_dir, branch)
            invalid.update(
                {
                    "status": "error",
                    "reviewRequired": True,
                    "reviewRequiredMessage": f"Pending worktree merge file is stale: {stale_reason}",
                    "sourceRepo": str(payload.get("source_repo") or payload.get("sourceRepo") or invalid["sourceRepo"]).strip()
                    or invalid["sourceRepo"],
                    "sourceBranch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "branch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "baseRef": str(payload.get("base_ref") or payload.get("baseRef") or "").strip(),
                    "headRef": str(payload.get("head_ref") or payload.get("headRef") or "").strip(),
                    "worktreeDir": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "worktree": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "patchPath": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "patch": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "pendingFile": pending_path.as_posix(),
                    "statusFile": pending_path.as_posix(),
                    "cleanupPath": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "cleanupMessage": "Cleanup state is unavailable until the marker is repaired.",
                    "cleanupState": "none",
                    "summary": "Pending worktree merge file is stale.",
                    "risk": "Fix or delete the stale pending merge file before applying any source-repo change.",
                    "changedFiles": [],
                    "changed_files": [],
                    "preflight": {},
                    "applyCheck": {},
                    "sourceRepoState": str(payload.get("source_repo_state") or payload.get("sourceRepoState") or ""),
                    "source_repo_state": str(payload.get("source_repo_state") or payload.get("sourceRepoState") or ""),
                    "sourceHead": str(payload.get("head_ref") or payload.get("headRef") or ""),
                    "source_head": str(payload.get("head_ref") or payload.get("headRef") or ""),
                    "expectedBaseRef": str(payload.get("base_ref") or payload.get("baseRef") or ""),
                    "expected_base_ref": str(payload.get("base_ref") or payload.get("baseRef") or ""),
                    "patchHash": str(payload.get("patch_hash") or payload.get("patchHash") or ""),
                    "patch_hash": str(payload.get("patch_hash") or payload.get("patchHash") or ""),
                    "pendingMarkerPath": pending_path.as_posix(),
                    "pending_marker_path": pending_path.as_posix(),
                    "resolutionActions": worktree_resolution_actions(
                        "stale_pending_marker",
                        pending_paths=[pending_path.as_posix()],
                        artifact_path=pending_path.as_posix(),
                    ),
                    "resolution_actions": worktree_resolution_actions(
                        "stale_pending_marker",
                        pending_paths=[pending_path.as_posix()],
                        artifact_path=pending_path.as_posix(),
                    ),
                    "runDir": str(payload.get("run_dir") or payload.get("runDir") or run_dir_value or "").strip(),
                    "runnerRc": 0,
                    "lastRc": 0,
                }
            )
            return _attach_recent_artifacts(invalid, recent_finalized_artifacts)

        return _attach_recent_artifacts(_worktree_status_payload(
            repo_root,
            run_dir,
            branch,
            status="pending review",
            artifact_path=pending_path,
            payload=payload,
            pending_path=pending_path,
        ), recent_finalized_artifacts)

    current_artifact_candidates = [
        (artifact_status, artifact_path)
        for artifact_status, artifact_path in artifact_candidates
        if artifact_status not in WORKTREE_FINALIZED_HISTORY_STATUSES
    ]
    selected_artifact = _worktree_select_artifact(current_artifact_candidates)
    if selected_artifact is not None:
        artifact_status, artifact_path = selected_artifact
        if artifact_status in {"applied_cleanup_failed", "discard_cleanup_failed"}:
            payload = _safe_json(artifact_path, {})
            if not isinstance(payload, dict):
                payload = {}
            return _attach_recent_artifacts(_worktree_status_payload(
                repo_root,
                run_dir,
                branch,
                status=artifact_status,
                artifact_path=artifact_path,
                payload=payload,
                pending_path=artifact_path.with_name("WORKTREE_MERGE_PENDING.json"),
            ), recent_finalized_artifacts)
        if artifact_status in {"apply_failed", "patch_not_applied", "not_applied"}:
            artifact_patch_path = (run_dir / "worktree.patch") if run_dir is not None else None
            artifact_patch_text = (
                artifact_patch_path.as_posix() if artifact_patch_path is not None and artifact_patch_path.exists() else ""
            )
            artifact_changed_files = summarize_worktree_diff(artifact_patch_path, allow_placeholder=True) if artifact_patch_text else []
            artifact_preflight: dict[str, Any] = {}
            if artifact_patch_path is not None and artifact_patch_path.exists():
                try:
                    if git_show_toplevel(repo_root):
                        artifact_preflight = summarize_worktree_preflight(
                            repo_root,
                            artifact_patch_path,
                            base_ref="",
                            pending_path=None,
                        )
                except Exception:
                    artifact_preflight = {}
            payload = _worktree_default_payload(repo_root, run_dir, branch)
            payload.update(
                {
                    "status": artifact_status,
                    "reviewRequired": True,
                    "reviewRequiredMessage": {
                        "apply_failed": "Worktree patch export failed.",
                        "patch_not_applied": "Worktree patch was exported but not auto-applied.",
                        "not_applied": "Worktree patch was not applied.",
                    }[artifact_status],
                    "summary": {
                        "apply_failed": "Worktree patch export failed.",
                        "patch_not_applied": "Worktree patch was exported but not auto-applied.",
                        "not_applied": "Worktree patch was not applied.",
                    }[artifact_status],
                    "risk": {
                        "apply_failed": "Manual recovery is required before the source repository can be reviewed.",
                        "patch_not_applied": "Review the exported patch before applying it manually.",
                        "not_applied": "Review the exported patch and apply it manually when ready.",
                    }[artifact_status],
                    "cleanupPath": "",
                    "cleanupMessage": "Cleanup state is unavailable because no merge marker was written.",
                    "cleanupDetails": {},
                    "cleanupAttempts": [],
                    "cleanupState": "none",
                    "statusFile": artifact_path.as_posix(),
                    "pendingFile": "",
                    "patchPath": artifact_patch_text,
                    "patch": artifact_patch_text,
                    "changedFiles": artifact_changed_files,
                    "changed_files": artifact_changed_files,
                    "preflight": artifact_preflight,
                    "applyCheck": artifact_preflight.get("applyCheck", {}) if isinstance(artifact_preflight, dict) else {},
                    "sourceRepoState": artifact_preflight.get("sourceRepoState", "") if isinstance(artifact_preflight, dict) else "",
                    "source_repo_state": artifact_preflight.get("sourceRepoState", "") if isinstance(artifact_preflight, dict) else "",
                    "sourceHead": artifact_preflight.get("sourceHead", "") if isinstance(artifact_preflight, dict) else "",
                    "source_head": artifact_preflight.get("sourceHead", "") if isinstance(artifact_preflight, dict) else "",
                    "expectedBaseRef": artifact_preflight.get("expectedBaseRef", "") if isinstance(artifact_preflight, dict) else "",
                    "expected_base_ref": artifact_preflight.get("expectedBaseRef", "") if isinstance(artifact_preflight, dict) else "",
                    "patchHash": artifact_preflight.get("patchHash", "") if isinstance(artifact_preflight, dict) else "",
                    "patch_hash": artifact_preflight.get("patchHash", "") if isinstance(artifact_preflight, dict) else "",
                    "pendingMarkerPath": "",
                    "pending_marker_path": "",
                    "runDir": run_dir.as_posix() if run_dir else "",
                }
            )
            return _attach_recent_artifacts(payload, recent_finalized_artifacts)

    return _attach_recent_artifacts(_worktree_default_payload(repo_root, run_dir, branch), recent_finalized_artifacts)


def build_snapshot(
    web: Any,
    repo: Path | str | None = None,
    *,
    config_path: str | None = None,
    bind_host: str = "127.0.0.1",
    bind_port: int = 8000,
    trusted_network: bool | None = None,
    runner_controller: Any | None = None,
    runner_controls_enabled: bool | None = None,
    runner_controls_source: str | None = None,
    runner_controls_disabled_reason: str = "",
    runner_control_busy: bool = False,
    runner_control_last_action: str = "",
    runner_control_last_message: str = "",
    runner_control_last_error: str = "",
    runner_controller_auto_build: bool = True,
    web_instance_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _repo_root = web._repo_root
    _load_config_payload = web._load_config_payload
    resolve_prompts_dir = web.resolve_prompts_dir
    default_prompts_dir = web.default_prompts_dir
    _resolve_runner_controls_enabled = web._resolve_runner_controls_enabled
    _web_redaction_active = web._web_redaction_active
    _lan_safety_blocks_mutations = web._lan_safety_blocks_mutations
    _web_instance_payload = web._web_instance_payload
    now_iso = web.now_iso
    _build_config_contract = web._build_config_contract
    _build_claude_advanced_diagnostics = web.build_claude_advanced_diagnostics
    _build_mcp_diagnostics = web.build_mcp_diagnostics
    _build_instance_health = web.build_instance_health
    _prompt_profile = web._prompt_profile
    resolve_goals_completion_level = web.resolve_goals_completion_level
    _build_goals_payload = web._build_goals_payload
    _load_prompt_items = web._load_prompt_items
    _branch_name = web._branch_name
    _build_runner_controller = web._build_runner_controller
    _controller_status_payload = web._controller_status_payload
    _resolve_latest_run_dir = web._resolve_latest_run_dir
    reconcile_cleanup_failed_artifacts = web.reconcile_cleanup_failed_artifacts
    _cycle_events = web._cycle_events
    _build_progress_payload = web._build_progress_payload
    _safe_json = web._safe_json
    _load_log_entries = web._load_log_entries
    _build_metrics_payload = web._build_metrics_payload
    _active_run_payload = web._active_run_payload
    _stage_payload = web._stage_payload
    _history_payload = web._history_payload
    _run_dirs = web._run_dirs
    _build_notifications = web._build_notifications
    _build_worktree_payload = web._build_worktree_payload
    _build_pr_queue_payload = web._build_pr_queue_payload
    _build_experience_payload = web._build_experience_payload
    build_todo_status = web.build_todo_status
    build_skills_status = web.build_skills_status
    selected_skill_ids_from_tasks = web.selected_skill_ids_from_tasks
    scan_worktree_diagnostics = web.scan_worktree_diagnostics
    _stage_output_stall_threshold_seconds = web._stage_output_stall_threshold_seconds
    _tail_text = web._tail_text
    _log_tail_source_catalog = web._log_tail_source_catalog
    _build_live_state_payload = web._build_live_state_payload
    _runner_control_payload = web._runner_control_payload
    _web_apply_redaction = web._web_apply_redaction
    _redact_web_goals_payload = web._redact_web_goals_payload
    _redact_web_backlog_payload = web._redact_web_backlog_payload
    _redact_web_log_payload = web._redact_web_log_payload
    _redact_web_stages_payload = web._redact_web_stages_payload
    _redact_config = web._redact_config
    _redact_web_config_payload = web._redact_web_config_payload
    _redact_web_config_contract = web._redact_web_config_contract
    _redact_web_prompts_payload = web._redact_web_prompts_payload
    _redact_web_notifications_payload = web._redact_web_notifications_payload
    _redact_web_history_payload = web._redact_web_history_payload
    _redact_web_pr_queue_payload = web._redact_web_pr_queue_payload
    _redact_web_todo_payload = web._redact_web_todo_payload
    _redact_web_runner_control = web._redact_web_runner_control
    _resolve_log_tail_source_record = web._resolve_log_tail_source_record
    _build_log_tail_payload = web._build_log_tail_payload
    _web_redaction_meta = web._web_redaction_meta
    _resolve_dashboard_active_task = web._resolve_dashboard_active_task
    buildSectionState = web.buildSectionState
    fallbackSectionMessage = web.fallbackSectionMessage
    _live_run_payload = web._live_run_payload
    _snapshot_freshness_timestamp_ms = web._snapshot_freshness_timestamp_ms
    _git_head_short = web._git_head_short
    REDACTED_VALUE = web.REDACTED_VALUE
    LAN_SAFETY_MUTATION_DISABLED_MESSAGE = web.LAN_SAFETY_MUTATION_DISABLED_MESSAGE

    repo_root = _repo_root(repo)
    cfg_path, cfg, cfg_source = _load_config_payload(repo_root, config_path)
    prompts_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
    if not prompts_dir:
        prompts_dir = default_prompts_dir(repo_root)
    if runner_controls_enabled is None and runner_controls_source is None and not runner_controls_disabled_reason:
        control_enabled, resolved_source, resolved_disabled_reason = _resolve_runner_controls_enabled(
            None,
            bind_host=bind_host,
            trusted_network=trusted_network,
        )
    else:
        control_enabled = bool(runner_controls_enabled)
        resolved_source = runner_controls_source or ("cli" if control_enabled else "default")
        resolved_disabled_reason = runner_controls_disabled_reason or (
            ""
            if control_enabled
            else "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
        )
    redaction_active = _web_redaction_active(bind_host)
    if redaction_active and _lan_safety_blocks_mutations(bind_host):
        control_enabled = False
        resolved_disabled_reason = LAN_SAFETY_MUTATION_DISABLED_MESSAGE
    web_instance = dict(web_instance_state) if isinstance(web_instance_state, dict) else _web_instance_payload(
        repo_root,
        bind_host=bind_host,
        bind_port=bind_port,
        state="primary",
        mode="read_write",
        reason="",
        created_at=now_iso(),
        runner_control_requested=bool(control_enabled),
        runner_control_enabled=bool(control_enabled),
        runner_control_state="enabled" if control_enabled else "disabled",
    )
    web_instance_state_value = str(web_instance.get("state") or "primary").strip().lower() or "primary"
    web_instance_mode = str(web_instance.get("mode") or "read_write").strip().lower() or "read_write"
    if web_instance_mode == "read_only":
        control_enabled = False
        resolved_disabled_reason = str(
            web_instance.get("reason") or resolved_disabled_reason or "Mutating web controls are disabled for this web console instance."
        ).strip()
        if not resolved_source:
            resolved_source = "web-lock"
    config_contract = _build_config_contract(
        repo_root,
        cfg,
        cfg_path,
        cfg_source,
        prompts_dir,
        save_enabled=control_enabled,
        save_endpoint="/api/config/save",
        save_requires_opt_in=True,
        restore_enabled=control_enabled,
        restore_endpoint="/api/config/restore",
        restore_requires_opt_in=True,
    )
    claude_advanced = _build_claude_advanced_diagnostics(cfg)
    mcp_diagnostics = _build_mcp_diagnostics(cfg)
    profile = _prompt_profile(cfg)
    goals_completion_level = resolve_goals_completion_level(cfg.get("goals_completion_level"))
    goals = _build_goals_payload(repo_root, completion_level=goals_completion_level)
    prompt_items = _load_prompt_items(repo_root, prompts_dir, profile=profile)
    todo = build_todo_status(repo_root, include_preview=True)
    branch = _branch_name(repo_root)
    controller = runner_controller
    if controller is None and runner_controller_auto_build:
        controller = _build_runner_controller(repo_root, cfg, cfg_path)
    controller_status = _controller_status_payload(controller)
    latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
    reconcile_cleanup_failed_artifacts(repo_root, latest_run_dir)
    logs_events = _cycle_events(latest_run_dir)
    progress = _build_progress_payload(
        repo=repo_root,
        run_dir=latest_run_dir,
        config=cfg,
        branch=branch,
        controller_status=controller_status,
        events=logs_events,
    )
    state = progress["state"] if isinstance(progress.get("state"), dict) else {"done": [], "failed": [], "warnings": []}
    backlog = progress["backlog"] if isinstance(progress.get("backlog"), dict) else {"items": [], "counts": {}, "selected_id": ""}
    selected_skill_ids = selected_skill_ids_from_tasks(backlog.get("items", []))
    skills_status = build_skills_status(repo_root, cfg.get("skills") or {}, run_dir=latest_run_dir, selected_skill_ids=selected_skill_ids)
    plugin_diagnostics = _safe_json(latest_run_dir / "PLUGIN_LOAD_DIAGNOSTICS.json", {}) if latest_run_dir else {}
    plugin_status_value = str(plugin_diagnostics.get("status") or "").strip().lower() if isinstance(plugin_diagnostics, dict) else ""
    plugin_status = {
        "enabled": _web_payload_bool(cfg.get("plugins_enabled"), default=False),
        "strict": _web_payload_bool(cfg.get("plugins_strict"), default=True),
        "allowlist": _web_payload_list(cfg.get("plugins_allowlist")),
        "diagnostics": plugin_diagnostics if isinstance(plugin_diagnostics, dict) else {},
        "status": plugin_status_value or ("disabled" if not _web_payload_bool(cfg.get("plugins_enabled"), default=False) else "unknown"),
        "diagnosticsPath": (latest_run_dir / "PLUGIN_LOAD_DIAGNOSTICS.json").as_posix() if latest_run_dir and (latest_run_dir / "PLUGIN_LOAD_DIAGNOSTICS.json").exists() else "",
        "diagnostics_path": (latest_run_dir / "PLUGIN_LOAD_DIAGNOSTICS.json").as_posix() if latest_run_dir and (latest_run_dir / "PLUGIN_LOAD_DIAGNOSTICS.json").exists() else "",
    }
    profile_effective = config_contract.get("profileEffective", config_contract.get("profile_effective", {}))
    enterprise_profile = {
        "profile": str(cfg.get("profile") or "personal"),
        "effective": profile_effective if isinstance(profile_effective, dict) else {},
        "profileEffective": profile_effective if isinstance(profile_effective, dict) else {},
    }
    run_summary = _safe_json(latest_run_dir / "run_summary.json", {}) if latest_run_dir else {}
    last_run_summary = _safe_json(latest_run_dir / "last_run_summary.json", {}) if latest_run_dir else {}
    log_entries = _load_log_entries(latest_run_dir)
    metrics = _build_metrics_payload(latest_run_dir, progress, controller_status=controller_status)
    active_run = _active_run_payload(
        repo=repo_root,
        run_dir=latest_run_dir,
        config=cfg,
        run_summary=run_summary,
        last_run_summary=last_run_summary,
        state=state,
        backlog=backlog.get("items", []),
        progress=progress,
        metrics=metrics,
        branch=branch,
        controller_status=controller_status,
    )
    active_run_quota = active_run.get("quota") if isinstance(active_run.get("quota"), dict) else {}
    metrics_quota = metrics.get("quota") if isinstance(metrics.get("quota"), dict) else {}
    if bool(active_run_quota.get("available")) and metrics_quota != active_run_quota:
        quota_window = str(active_run_quota.get("window") or "")
        quota_used = active_run_quota.get("used")
        metrics = dict(metrics)
        metrics["quota"] = dict(active_run_quota)
        metrics["quota_available"] = True
        metrics["quotaAvailable"] = True
        metrics["quota_window"] = quota_window
        metrics["quotaWindow"] = quota_window
        metrics["quota_used"] = quota_used
        metrics["quotaUsed"] = quota_used
    state_counts = progress.get("state_counts") if isinstance(progress.get("state_counts"), dict) else {
        "done": 0,
        "failed": 0,
        "warnings": 0,
    }
    stages = _stage_payload(
        repo_root,
        active_run,
        progress,
        cfg,
        run_dir=latest_run_dir,
        run_summary=run_summary,
        last_run_summary=last_run_summary,
        controller_status=controller_status,
        events=logs_events,
    )
    history = _history_payload(
        repo_root,
        _run_dirs(repo_root),
        branch=branch,
        completion_level=resolve_goals_completion_level(cfg.get("goals_completion_level")),
    )
    notifications = _build_notifications(
        run_id=active_run["id"],
        started_at_ms=int(active_run.get("startedAt") or 0),
        branch=active_run.get("branch") or branch,
        active_status=str(progress.get("run_status") or "idle"),
        state=state,
        backlog=backlog.get("items", []),
        events=logs_events,
        final_reason=str(progress.get("final_reason") or ""),
    )
    worktree = _build_worktree_payload(repo_root, latest_run_dir, branch=branch)
    pr_queue = _build_pr_queue_payload(repo_root, detail=False)
    experience = _build_experience_payload(repo_root, latest_run_dir)
    worktree_diagnostics = scan_worktree_diagnostics(repo_root)
    log_tail = _tail_text((latest_run_dir / "cycle_summary.log") if latest_run_dir else Path(""), 80)
    log_source_catalog = _log_tail_source_catalog(latest_run_dir)
    log_entries_source = {
        "id": "api_logs_structured" if logs_events else "run_log",
        "label": "/api/logs structured events" if logs_events else "run.log",
        "kind": "structured" if logs_events else "log",
    }
    log_files = {
        str(source.get("id") or "").strip(): str(source.get("path") or "")
        for source in log_source_catalog
        if str(source.get("id") or "").strip()
    }
    if latest_run_dir:
        log_files["metrics"] = (latest_run_dir / "metrics.jsonl").as_posix()
    live_state = _build_live_state_payload(
        controller_status,
        progress=progress,
        active_run=active_run,
        controller_available=controller is not None,
    )
    runner_control = _runner_control_payload(
        controller,
        repo=repo_root,
        enabled=control_enabled,
        source=runner_controls_source or resolved_source,
        disabled_reason=resolved_disabled_reason,
        config_path=cfg_path.as_posix(),
        current_run_dir=latest_run_dir.as_posix() if latest_run_dir else "",
        last_action=runner_control_last_action,
        last_message=runner_control_last_message,
        last_error=runner_control_last_error,
        live_state=live_state,
        run_status=str(progress.get("run_status") or "idle"),
        execution_status=str(progress.get("execution_status") or progress.get("executionStatus") or ""),
        project_complete=bool(progress.get("project_complete", False)),
        project_status=str(progress.get("project_status") or progress.get("projectStatus") or ""),
        goals_complete=bool(progress.get("goals_complete", False)),
        backlog_complete=bool(progress.get("backlog_complete", False)),
        busy=bool(runner_control_busy),
        cfg=cfg,
        cfg_path=cfg_path,
        redact_sensitive=redaction_active,
        web_instance=web_instance,
    )
    goals = _web_apply_redaction(goals, active=redaction_active, redactor=_redact_web_goals_payload)
    backlog = _web_apply_redaction(backlog, active=redaction_active, redactor=_redact_web_backlog_payload)
    progress = dict(progress)
    progress["goals"] = goals
    progress["backlog"] = backlog
    log_payload = _web_apply_redaction(
        {"entries": log_entries, "tail": log_tail, "files": log_files},
        active=redaction_active,
        redactor=_redact_web_log_payload,
    )
    logs_redaction = log_payload.get("redaction") if isinstance(log_payload, dict) else {}
    log_entries = list(log_payload.get("entries") or []) if isinstance(log_payload, dict) else list(log_entries)
    log_tail = str(log_payload.get("tail") or "") if isinstance(log_payload, dict) else log_tail
    log_files = dict(log_payload.get("files") or log_files) if isinstance(log_payload, dict) else log_files
    stages = _web_apply_redaction(stages, active=redaction_active, redactor=_redact_web_stages_payload)
    config_payload = _web_apply_redaction(
        {
            "path": cfg_path.as_posix(),
            "source": cfg_source,
            "data": _redact_config(cfg),
            "resolved_prompts_dir": prompts_dir.as_posix(),
            "meta": {
                "path": cfg_path.as_posix(),
                "source": cfg_source,
                "resolved_prompts_dir": prompts_dir.as_posix(),
            },
        },
        active=redaction_active,
        redactor=_redact_web_config_payload,
    )
    config_contract = _web_apply_redaction(config_contract, active=redaction_active, redactor=_redact_web_config_contract)
    prompt_payload = _web_apply_redaction(
        {"dir": prompts_dir.as_posix(), "items": prompt_items},
        active=redaction_active,
        redactor=_redact_web_prompts_payload,
    )
    prompts_redaction = prompt_payload.get("redaction") if isinstance(prompt_payload, dict) else {}
    prompt_items = list(prompt_payload.get("items") or []) if isinstance(prompt_payload, dict) else list(prompt_items)
    notifications = _web_apply_redaction(notifications, active=redaction_active, redactor=_redact_web_notifications_payload)
    history = _web_apply_redaction(history, active=redaction_active, redactor=_redact_web_history_payload)
    pr_queue = _web_apply_redaction(pr_queue, active=redaction_active, redactor=_redact_web_pr_queue_payload)
    todo = _web_apply_redaction(todo, active=redaction_active, redactor=_redact_web_todo_payload)
    runner_control = _web_apply_redaction(
        runner_control,
        active=redaction_active,
        redactor=lambda value: _redact_web_runner_control(value, redact_start_options=True),
    )
    instance_health = _build_instance_health(
        repo_root,
        run_dir=latest_run_dir,
        web_instance=web_instance,
        live_state=live_state,
        runner_control=runner_control,
    )
    log_summary_payload: dict[str, Any] = {
        "source": {
            "id": "",
            "label": "",
            "path": "",
            "name": "",
            "exists": False,
            "available": False,
            "selected": False,
            "kind": "log",
            "unavailable_reason": "missing",
        },
        "source_id": "",
        "selected_source_id": "",
        "sources": log_source_catalog,
        "cursor": 0,
        "nextCursor": 0,
        "state": "empty",
        "ok": False,
        "malformedLines": 0,
        "eof": False,
        "lastLine": None,
        "last_activity_at": None,
        "lastActivityAt": None,
        "output_stalled": False,
        "outputStalled": False,
        "no_output_minutes": None,
        "noOutputMinutes": None,
    }
    log_source_record = _resolve_log_tail_source_record(latest_run_dir)
    log_source_path = (
        Path(str(log_source_record.get("path") or ""))
        if log_source_record and str(log_source_record.get("path") or "").strip()
        else None
    )
    if log_source_path is not None:
        try:
            log_tail_source_payload = _build_log_tail_payload(
                log_source_path,
                source=log_source_record,
                sources=log_source_catalog,
                cursor=None,
                max_lines=1,
                live=str(progress.get("run_status") or "idle").strip().lower() == "running",
                stalled_threshold_seconds=_stage_output_stall_threshold_seconds(cfg),
            )
            log_summary_payload = {
                "source": log_tail_source_payload.get("source", {}),
                "source_id": str(log_tail_source_payload.get("source_id") or ""),
                "selected_source_id": str(log_tail_source_payload.get("selected_source_id") or ""),
                "sources": list(log_tail_source_payload.get("sources") or log_source_catalog),
                "cursor": int(log_tail_source_payload.get("next_cursor") or 0),
                "nextCursor": int(log_tail_source_payload.get("next_cursor") or 0),
                "state": str(log_tail_source_payload.get("state") or "empty"),
                "ok": bool(log_tail_source_payload.get("ok", False)),
                "malformedLines": int(log_tail_source_payload.get("malformed_lines") or 0),
                "eof": bool(log_tail_source_payload.get("eof", False)),
                "last_line": log_tail_source_payload.get("last_line"),
                "lastLine": log_tail_source_payload.get("lastLine") or log_tail_source_payload.get("last_line"),
                "last_activity_at": log_tail_source_payload.get("last_activity_at"),
                "lastActivityAt": log_tail_source_payload.get("lastActivityAt", log_tail_source_payload.get("last_activity_at")),
                "output_stalled": bool(log_tail_source_payload.get("output_stalled", False)),
                "outputStalled": bool(log_tail_source_payload.get("outputStalled", log_tail_source_payload.get("output_stalled", False))),
                "no_output_minutes": log_tail_source_payload.get("no_output_minutes"),
                "noOutputMinutes": log_tail_source_payload.get("noOutputMinutes", log_tail_source_payload.get("no_output_minutes")),
            }
        except Exception:
            log_summary_payload = {
                "source": {
                    "id": str(log_source_record.get("id") or "") if log_source_record else "",
                    "label": str(log_source_record.get("label") or "") if log_source_record else "",
                    "path": log_source_path.as_posix(),
                    "name": log_source_path.name,
                    "exists": False,
                    "available": False,
                    "selected": True,
                    "kind": str(log_source_record.get("kind") or "log") if log_source_record else "log",
                    "unavailable_reason": "read_error",
                },
                "source_id": str(log_source_record.get("id") or "") if log_source_record else "",
                "selected_source_id": str(log_source_record.get("id") or "") if log_source_record else "",
                "sources": list(log_source_catalog),
                "cursor": 0,
                "nextCursor": 0,
                "state": "read_error",
                "ok": False,
                "malformedLines": 0,
                "eof": False,
                "lastLine": None,
                "last_activity_at": None,
                "lastActivityAt": None,
                "output_stalled": False,
                "outputStalled": False,
                "no_output_minutes": None,
                "noOutputMinutes": None,
            }
    log_summary_payload = _web_apply_redaction(log_summary_payload, active=redaction_active, redactor=_redact_web_log_payload)
    if redaction_active:
        progress["redaction"] = _web_redaction_meta("goals", "backlog")
    else:
        logs_redaction = {}
        prompts_redaction = {}
    resolved_active_task = _resolve_dashboard_active_task(
        repo_root,
        run_dir=latest_run_dir,
        backlog=backlog,
        controller_status=controller_status,
        run_summary=run_summary,
        last_run_summary=last_run_summary,
        events=logs_events,
        repo_branch=branch,
    )
    if isinstance(resolved_active_task, dict):
        resolved_task_id = str(resolved_active_task.get("task_id") or "").strip()
        resolved_task_title = str(resolved_active_task.get("task_title") or "").strip()
        resolved_attempt = resolved_active_task.get("attempt")
        resolved_branch = str(resolved_active_task.get("branch") or "").strip()
        resolved_cycle = resolved_active_task.get("cycle")
        resolved_step = resolved_active_task.get("step")
        if resolved_task_id:
            active_run["task"] = resolved_task_id
            progress["current_task_id"] = resolved_task_id
        if resolved_task_title:
            active_run["taskTitle"] = resolved_task_title
            progress["current_task_title"] = resolved_task_title
        if resolved_attempt is not None:
            active_run["attempt"] = resolved_attempt
            progress["attempt"] = resolved_attempt
        if resolved_branch:
            active_run["branch"] = resolved_branch
            progress["branch"] = resolved_branch
        if resolved_cycle is not None and progress.get("cycle") is None:
            progress["cycle"] = resolved_cycle
        if resolved_step is not None and progress.get("step") is None:
            progress["step"] = resolved_step
    active_run_empty = active_run["status"] == "idle" and not active_run.get("task") and not active_run.get("startedAt")
    runner_control_status = runner_control.get("status") if isinstance(runner_control.get("status"), dict) else {}
    runner_control_status_reason = str(runner_control_status.get("reason") or "").strip()
    if runner_control.get("busy"):
        runner_control_state = "busy"
    elif web_instance_state_value == "duplicate":
        runner_control_state = "duplicate"
    elif (
        runner_control.get("last_error")
        or runner_control_status_reason.startswith("status_error:")
        or not runner_control.get("controller_available")
    ):
        runner_control_state = "error"
    elif not runner_control.get("enabled"):
        runner_control_state = "disabled"
    elif runner_control.get("last_message"):
        runner_control_state = "success"
    else:
        runner_control_state = "ready"
    goals_summary = goals.get("summary") if isinstance(goals.get("summary"), dict) else {}
    goals_total = int(goals_summary.get("total") or 0)
    if not goals_total:
        goals_items = goals.get("items") if isinstance(goals.get("items"), dict) else {}
        goals_total = sum(len(items) for items in goals_items.values()) if isinstance(goals_items, dict) else 0
    has_metrics = bool(
        metrics.get("tokens24h")
        or metrics.get("success24h")
        or metrics.get("budget")
        or metrics.get("last_stage")
        or metrics.get("quota_used") is not None
        or metrics.get("budget_used") is not None
        or metrics.get("tokens_available")
        or metrics.get("budget_available")
        or metrics.get("quota_available")
    )
    active_run_section_state = buildSectionState(
        "activeRun",
        "empty" if active_run_empty else "ready",
        fallbackSectionMessage("activeRun") if active_run_empty else "",
    )
    stages_section_state = buildSectionState(
        "stages",
        "ready" if len(stages) >= 3 else ("partial" if stages else "empty"),
        "" if len(stages) >= 3 else ("Only some lifecycle records were published." if stages else fallbackSectionMessage("stages")),
    )
    backlog_section_state = buildSectionState(
        "backlog",
        "ready" if backlog.get("items") else "empty",
        "" if backlog.get("items") else fallbackSectionMessage("backlog"),
    )
    goals_section_state = buildSectionState("goals", "ready" if goals_total else "empty", "" if goals_total else fallbackSectionMessage("goals"))
    config_section_state = buildSectionState("config", "ready" if config_contract.get("schema") else "empty", "")
    claude_status = str(claude_advanced.get("status") or "unknown").strip().lower()
    claude_section_state = buildSectionState(
        "claude",
        "ready" if claude_status == "ok" else "partial" if claude_status == "warning" else "error",
        "" if claude_status == "ok" else fallbackSectionMessage("claude"),
    )
    mcp_status = str(mcp_diagnostics.get("status") or "unknown").strip().lower()
    mcp_section_state = buildSectionState(
        "mcp",
        "ready" if mcp_status == "ok" else "partial" if mcp_status == "warning" else "error",
        "" if mcp_status == "ok" else fallbackSectionMessage("mcp"),
    )
    prompts_section_state = buildSectionState(
        "prompts",
        "ready" if prompt_items else "empty",
        "" if prompt_items else fallbackSectionMessage("prompts"),
    )
    todo_state = str(todo.get("state") or "missing").strip().lower() if isinstance(todo, dict) else "missing"
    todo_section_state = buildSectionState(
        "todo",
        "ready" if todo_state == "ready" else "partial" if todo_state in {"empty", "stale"} else "empty" if todo_state == "missing" else "error",
        str(todo.get("message") or fallbackSectionMessage("todo")) if isinstance(todo, dict) else fallbackSectionMessage("todo"),
    )
    skills_enabled = bool(skills_status.get("enabled")) if isinstance(skills_status, dict) else False
    skills_warnings = skills_status.get("warnings") if isinstance(skills_status, dict) and isinstance(skills_status.get("warnings"), list) else []
    skills_missing = skills_status.get("missing_skill_ids") if isinstance(skills_status, dict) and isinstance(skills_status.get("missing_skill_ids"), list) else []
    skills_section_state = buildSectionState(
        "skills",
        "partial" if skills_enabled and (skills_warnings or skills_missing) else "ready" if skills_enabled else "disabled",
        "; ".join(str(item) for item in (skills_warnings or skills_missing)[:3]) if skills_enabled and (skills_warnings or skills_missing) else ("" if skills_enabled else "Skills are disabled in config."),
    )
    plugins_section_state = buildSectionState(
        "plugins",
        "error" if plugin_status["status"] == "failed" else "partial" if plugin_status["status"] in {"partial", "unknown"} else "ready" if plugin_status["enabled"] else "disabled",
        "" if plugin_status["status"] == "ok" else (str(plugin_diagnostics.get("error") or "") if isinstance(plugin_diagnostics, dict) and plugin_diagnostics.get("error") else fallbackSectionMessage("plugins")),
    )
    enterprise_section_state = buildSectionState(
        "enterprise",
        "ready" if str(enterprise_profile.get("profile") or "").strip().lower() == "enterprise" else "disabled",
        "" if str(enterprise_profile.get("profile") or "").strip().lower() == "enterprise" else "Enterprise profile is not active.",
    )
    logs_section_state = buildSectionState(
        "logs",
        "ready" if log_entries else "empty",
        "" if log_entries else fallbackSectionMessage("logs"),
    )
    notifications_section_state = buildSectionState(
        "notifications",
        "ready" if notifications else "empty",
        "" if notifications else fallbackSectionMessage("notifications"),
    )
    metrics_section_state = buildSectionState(
        "metrics",
        "ready" if has_metrics else "empty",
        "" if has_metrics else fallbackSectionMessage("metrics"),
    )
    history_section_state = buildSectionState(
        "history",
        "ready" if history.get("items") else "empty",
        "" if history.get("items") else fallbackSectionMessage("history"),
    )
    pr_queue_items = pr_queue.get("items") if isinstance(pr_queue.get("items"), list) else []
    pr_queue_section_state = buildSectionState(
        "prQueue",
        "ready" if pr_queue_items else "empty",
        "" if pr_queue_items else fallbackSectionMessage("prQueue"),
    )
    experience_state = str(experience.get("state") or "").strip().lower() if isinstance(experience, dict) else ""
    experience_section_status = (
        "ready"
        if experience_state == "ready"
        else "partial" if experience_state == "partial" else "empty"
    )
    experience_section_state = buildSectionState(
        "experience",
        experience_section_status,
        str(experience.get("message") or fallbackSectionMessage("experience")) if isinstance(experience, dict) else fallbackSectionMessage("experience"),
    )
    worktree_status = str(worktree.get("status") or "none").strip()
    if worktree_status == "none":
        worktree_section_status = "empty"
    elif worktree_status == "error":
        worktree_section_status = "error"
    elif worktree_status in {"applied", "discarded"}:
        worktree_section_status = "ready"
    else:
        worktree_section_status = "partial"
    worktree_section_message = (
        worktree.get("reviewRequiredMessage")
        or worktree.get("cleanupMessage")
        or worktree.get("summary")
        or (fallbackSectionMessage("worktree") if worktree_status == "none" else "")
    )
    worktree_section_state = buildSectionState("worktree", worktree_section_status, worktree_section_message)
    runner_control_section_state = buildSectionState(
        "runnerControl",
        runner_control_state,
        runner_control.get("message") or fallbackSectionMessage("runnerControl"),
    )
    instance_health_status = str(instance_health.get("status") or "ok").strip().lower()
    instance_health_section_state = buildSectionState(
        "instanceHealth",
        "error" if instance_health_status == "error" else "partial" if instance_health_status == "warning" else "ready",
        "" if instance_health_status == "ok" else "Instance health diagnostics need operator review.",
    )
    live_run = _live_run_payload(
        repo=repo_root,
        branch=branch,
        latest_run_dir=latest_run_dir,
        active_run=active_run,
        progress=progress,
        stages=stages,
        logs={
            "entries": log_entries,
            "tail": log_tail,
            "files": log_files,
            "source": log_summary_payload.get("source", {}),
            "source_id": log_summary_payload.get("source_id", ""),
            "selected_source_id": log_summary_payload.get("selected_source_id", ""),
            "sources": log_summary_payload.get("sources", []),
            "cursor": log_summary_payload.get("cursor", 0),
            "nextCursor": log_summary_payload.get("nextCursor", 0),
            "state": log_summary_payload.get("state", "empty"),
            "ok": log_summary_payload.get("ok", False),
            "malformedLines": log_summary_payload.get("malformedLines", 0),
            "eof": bool(log_summary_payload.get("eof", False)),
            "last_line": log_summary_payload.get("last_line"),
            "lastLine": log_summary_payload.get("lastLine"),
            "last_activity_at": log_summary_payload.get("last_activity_at"),
            "lastActivityAt": log_summary_payload.get("lastActivityAt"),
            "output_stalled": bool(log_summary_payload.get("output_stalled", False)),
            "outputStalled": bool(log_summary_payload.get("outputStalled", False)),
            "no_output_minutes": log_summary_payload.get("no_output_minutes"),
            "noOutputMinutes": log_summary_payload.get("noOutputMinutes"),
        },
        notifications=notifications,
        runner_control=runner_control,
        live_state=live_state,
        controller_status=controller_status,
    )
    live_identity = live_run.get("identity") if isinstance(live_run.get("identity"), dict) else {}
    if isinstance(live_identity, dict):
        live_identity["webInstanceState"] = web_instance_state_value
        live_identity["web_instance_state"] = web_instance_state_value
        live_identity["webInstanceMode"] = web_instance_mode
        live_identity["web_instance_mode"] = web_instance_mode
        live_identity["webInstanceReadOnly"] = bool(web_instance_mode == "read_only")
        live_identity["web_instance_read_only"] = bool(web_instance_mode == "read_only")
        live_identity["webInstanceDuplicate"] = bool(web_instance_state_value == "duplicate")
        live_identity["web_instance_duplicate"] = bool(web_instance_state_value == "duplicate")
        live_identity["webInstance"] = dict(web_instance)
        live_identity["web_instance"] = dict(web_instance)
    snapshot_freshness_at = _snapshot_freshness_timestamp_ms(
        latest_run_dir,
        active_run=active_run,
        controller_status=controller_status,
        logs={"entries": log_entries},
        notifications=notifications,
    ) or int(datetime.now(timezone.utc).timestamp() * 1000)
    snapshot_refresh = {
        "status": "ready",
        "lastUpdatedAt": snapshot_freshness_at,
        "last_updated_at": snapshot_freshness_at,
        "lastSuccessAt": snapshot_freshness_at,
        "last_success_at": snapshot_freshness_at,
        "latestRunDir": latest_run_dir.as_posix() if latest_run_dir else "",
        "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else "",
        "stale": False,
        "staleReasons": [],
        "stale_reasons": [],
    }

    return {
        "ok": True,
        "repo": {
            "path": repo_root.as_posix(),
            "name": repo_root.name or repo_root.as_posix().rsplit("/", 1)[-1],
            "head": _git_head_short(repo_root),
            "branch": branch or "HEAD",
        },
        "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else None,
        "snapshot_refresh": snapshot_refresh,
        "snapshotRefresh": snapshot_refresh,
        "active_run": active_run,
        "stages": stages,
        "backlog": backlog,
        "goals": goals,
        "logs": {
            "entries": log_entries,
            "tail": log_tail,
            "files": log_files,
            "entries_source_id": log_entries_source.get("id", ""),
            "entries_source_label": log_entries_source.get("label", ""),
            "entries_source_kind": log_entries_source.get("kind", "log"),
            "source": log_summary_payload.get("source", {}),
            "source_id": log_summary_payload.get("source_id", ""),
            "selected_source_id": log_summary_payload.get("selected_source_id", ""),
            "sources": log_summary_payload.get("sources", []),
            "eof": bool(log_summary_payload.get("eof", False)),
            "last_line": log_summary_payload.get("last_line"),
            "lastLine": log_summary_payload.get("lastLine"),
            "last_activity_at": log_summary_payload.get("last_activity_at"),
            "lastActivityAt": log_summary_payload.get("lastActivityAt"),
            "output_stalled": bool(log_summary_payload.get("output_stalled", False)),
            "outputStalled": bool(log_summary_payload.get("outputStalled", False)),
            "no_output_minutes": log_summary_payload.get("no_output_minutes"),
            "noOutputMinutes": log_summary_payload.get("noOutputMinutes"),
            "redaction": logs_redaction,
        },
        "config": config_payload,
        "config_contract": config_contract,
        "claude_advanced": claude_advanced,
        "claudeAdvanced": claude_advanced,
        "mcp_diagnostics": mcp_diagnostics,
        "mcpDiagnostics": mcp_diagnostics,
        "prompts": {
            "dir": prompt_payload.get("dir", prompts_dir.as_posix()) if isinstance(prompt_payload, dict) else prompts_dir.as_posix(),
            "exists": prompts_dir.exists(),
            "items": prompt_items,
            "redaction": prompts_redaction,
        },
        "history": history,
        "metrics": metrics,
        "notifications": notifications,
        "todo": todo,
        "skills_status": skills_status,
        "skillsStatus": skills_status,
        "plugin_status": plugin_status,
        "pluginStatus": plugin_status,
        "enterprise_profile": enterprise_profile,
        "enterpriseProfile": enterprise_profile,
        "experience": experience,
        "pr_queue": pr_queue,
        "prQueue": pr_queue,
        "worktree": worktree,
        "worktree_diagnostics": worktree_diagnostics,
        "instance_health": instance_health,
        "instanceHealth": instance_health,
        "runner_control": runner_control,
        "web_instance": web_instance,
        "webInstance": web_instance,
        "liveRun": live_run,
        "redaction": {
            "active": redaction_active,
            "placeholder": REDACTED_VALUE,
            "scope": "lan" if redaction_active else "local",
        },
        "progress": {
            "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else None,
            "run_status": progress.get("run_status"),
            "tasks_done": progress.get("tasks_done", 0),
            "tasks_total": progress.get("tasks_total", 0),
            "tasks_failed": progress.get("tasks_failed", 0),
            "state_counts": state_counts,
            "stateCounts": state_counts,
            "progress": progress.get("progress"),
            "progress_available": progress.get("progress_available", False),
            "current_task_id": progress.get("current_task_id", ""),
            "current_task_title": progress.get("current_task_title", ""),
            "attempt": progress.get("attempt"),
            "branch": progress.get("branch", active_run.get("branch", "")),
            "worktree_mode": progress.get("worktree_mode", ""),
            "execution_status": progress.get("execution_status", ""),
            "executionStatus": progress.get("executionStatus", progress.get("execution_status", "")),
            "completion_status": progress.get("completion_status", ""),
            "completionStatus": progress.get("completionStatus", progress.get("completion_status", "")),
            "completion_reason": progress.get("completion_reason", ""),
            "completionReason": progress.get("completionReason", progress.get("completion_reason", "")),
            "project_complete": bool(progress.get("project_complete", False)),
            "projectComplete": bool(progress.get("projectComplete", progress.get("project_complete", False))),
            "project_status": progress.get("project_status", ""),
            "projectStatus": progress.get("projectStatus", progress.get("project_status", "")),
            "goals_complete": bool(progress.get("goals_complete", False)),
            "goalsComplete": bool(progress.get("goalsComplete", progress.get("goals_complete", False))),
            "backlog_complete": bool(progress.get("backlog_complete", False)),
            "backlogComplete": bool(progress.get("backlogComplete", progress.get("backlog_complete", False))),
            "goals": progress.get("goals", {}),
            "backlog": backlog,
            "final_reason": progress.get("final_reason", ""),
            "final_rc": progress.get("final_rc"),
            "state": state,
        },
        "execution_status": progress.get("execution_status", ""),
        "executionStatus": progress.get("executionStatus", progress.get("execution_status", "")),
        "project_complete": bool(progress.get("project_complete", False)),
        "projectComplete": bool(progress.get("projectComplete", progress.get("project_complete", False))),
        "project_status": progress.get("project_status", ""),
        "projectStatus": progress.get("projectStatus", progress.get("project_status", "")),
        "goals_complete": bool(progress.get("goals_complete", False)),
        "goalsComplete": bool(progress.get("goalsComplete", progress.get("goals_complete", False))),
        "backlog_complete": bool(progress.get("backlog_complete", False)),
        "backlogComplete": bool(progress.get("backlogComplete", progress.get("backlog_complete", False))),
        "sectionState": {
            "activeRun": active_run_section_state,
            "stages": stages_section_state,
            "backlog": backlog_section_state,
            "goals": goals_section_state,
            "config": config_section_state,
            "claude": claude_section_state,
            "mcp": mcp_section_state,
            "prompts": prompts_section_state,
            "logs": logs_section_state,
            "notifications": notifications_section_state,
            "todo": todo_section_state,
            "skills": skills_section_state,
            "plugins": plugins_section_state,
            "enterprise": enterprise_section_state,
            "metrics": metrics_section_state,
            "history": history_section_state,
            "experience": experience_section_state,
            "prQueue": pr_queue_section_state,
            "worktree": worktree_section_state,
            "runnerControl": runner_control_section_state,
            "instanceHealth": instance_health_section_state,
        },
        "run_summary": run_summary,
        "last_run_summary": last_run_summary,
    }


_DASHBOARD_STATUS_LOG_ENTRY_LIMIT = 12
_DASHBOARD_STATUS_NOTIFICATION_LIMIT = 12


def _dashboard_goals_payload(goals: Any) -> dict[str, Any]:
    raw = goals if isinstance(goals, dict) else {}
    compact = dict(raw)
    compact.pop("raw_text", None)
    compact.pop("rawText", None)
    return compact


def _dashboard_logs_payload(logs: Any) -> dict[str, Any]:
    raw = logs if isinstance(logs, dict) else {}
    entries = list(raw.get("entries") or [])
    return {
        "entries": entries[-_DASHBOARD_STATUS_LOG_ENTRY_LIMIT:],
        "entries_source_id": raw.get("entries_source_id", ""),
        "entries_source_label": raw.get("entries_source_label", ""),
        "entries_source_kind": raw.get("entries_source_kind", "log"),
        "source": raw.get("source", {}),
        "source_id": raw.get("source_id", ""),
        "selected_source_id": raw.get("selected_source_id", ""),
        "sources": list(raw.get("sources") or []),
        "eof": bool(raw.get("eof", False)),
        "last_line": raw.get("last_line"),
        "lastLine": raw.get("lastLine", raw.get("last_line")),
        "last_activity_at": raw.get("last_activity_at"),
        "lastActivityAt": raw.get("lastActivityAt", raw.get("last_activity_at")),
        "output_stalled": bool(raw.get("output_stalled", False)),
        "outputStalled": bool(raw.get("outputStalled", raw.get("output_stalled", False))),
        "no_output_minutes": raw.get("no_output_minutes"),
        "noOutputMinutes": raw.get("noOutputMinutes", raw.get("no_output_minutes")),
        "redaction": raw.get("redaction", {}),
    }


def _dashboard_config_payload(config: Any, config_contract: Any) -> dict[str, Any]:
    raw = config if isinstance(config, dict) else {}
    contract = config_contract if isinstance(config_contract, dict) else {}
    contract_meta = contract.get("meta") if isinstance(contract.get("meta"), dict) else {}
    resolved_prompts_dir = str(
        raw.get("resolved_prompts_dir")
        or contract.get("resolved_prompts_dir")
        or contract_meta.get("resolved_prompts_dir")
        or ""
    )
    return {
        "path": raw.get("path", ""),
        "source": raw.get("source", ""),
        "resolved_prompts_dir": resolved_prompts_dir,
        "meta": {
            "path": raw.get("path", ""),
            "source": raw.get("source", ""),
            "resolved_prompts_dir": resolved_prompts_dir,
        },
    }


def _dashboard_config_contract_payload(config_contract: Any) -> dict[str, Any]:
    raw = config_contract if isinstance(config_contract, dict) else {}
    redaction = raw.get("redaction") if isinstance(raw.get("redaction"), dict) else {}
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    resolved_prompts_dir = str(raw.get("resolved_prompts_dir") or meta.get("resolved_prompts_dir") or "")
    return {
        "path": raw.get("path", ""),
        "source": raw.get("source", ""),
        "resolved_prompts_dir": resolved_prompts_dir,
        "values": {},
        "defaults": {},
        "schema": {},
        "groups": [],
        "restart_required_paths": [],
        "backups": [],
        "profile_effective": raw.get("profile_effective", {}),
        "profileEffective": raw.get("profileEffective", raw.get("profile_effective", {})),
        "redaction": {
            "placeholder": redaction.get("placeholder", "[redacted]"),
            "paths": list(redaction.get("paths") or []),
            "tokens": list(redaction.get("tokens") or []),
        },
        "meta": {
            "path": raw.get("path", ""),
            "source": raw.get("source", ""),
            "resolved_prompts_dir": resolved_prompts_dir,
            "save_enabled": bool(meta.get("save_enabled", False)),
            "save_endpoint": meta.get("save_endpoint", "/api/config/save"),
            "save_requires_opt_in": bool(meta.get("save_requires_opt_in", True)),
            "restore_enabled": bool(meta.get("restore_enabled", False)),
            "restore_endpoint": meta.get("restore_endpoint", "/api/config/restore"),
            "restore_requires_opt_in": bool(meta.get("restore_requires_opt_in", True)),
        },
    }


def _dashboard_prompts_payload(prompts: Any) -> dict[str, Any]:
    raw = prompts if isinstance(prompts, dict) else {}
    return {
        "dir": raw.get("dir", ""),
        "exists": bool(raw.get("exists", False)),
        "items": [],
        "redaction": raw.get("redaction", {}),
    }


def _dashboard_history_payload(history: Any) -> dict[str, Any]:
    raw = history if isinstance(history, dict) else {}
    compact = dict(raw)
    compact["items"] = []
    return compact


def _dashboard_live_run_log_payload(log: Any) -> dict[str, Any]:
    raw = log if isinstance(log, dict) else {}
    entries = list(raw.get("entries") or [])
    return {
        "source": raw.get("source", {}),
        "cursor": raw.get("cursor", 0),
        "nextCursor": raw.get("nextCursor", raw.get("next_cursor", raw.get("cursor", 0))),
        "next_cursor": raw.get("next_cursor", raw.get("nextCursor", raw.get("cursor", 0))),
        "state": raw.get("state", "empty"),
        "entries": entries[-_DASHBOARD_STATUS_LOG_ENTRY_LIMIT:],
        "tail": "",
        "files": {},
        "ok": bool(raw.get("ok", False)),
        "malformedLines": int(raw.get("malformedLines") or 0),
        "malformed_lines": int(raw.get("malformed_lines") or raw.get("malformedLines") or 0),
        "eof": bool(raw.get("eof", False)),
        "lastLine": raw.get("lastLine", raw.get("last_line")),
        "last_line": raw.get("last_line", raw.get("lastLine")),
        "outputStalled": bool(raw.get("outputStalled", raw.get("output_stalled", False))),
        "output_stalled": bool(raw.get("output_stalled", raw.get("outputStalled", False))),
        "noOutputMinutes": raw.get("noOutputMinutes", raw.get("no_output_minutes")),
        "no_output_minutes": raw.get("no_output_minutes", raw.get("noOutputMinutes")),
        "lastActivityAt": raw.get("lastActivityAt", raw.get("last_activity_at")),
        "last_activity_at": raw.get("last_activity_at", raw.get("lastActivityAt")),
    }


def _dashboard_live_run_notifications_payload(notifications: Any) -> dict[str, Any]:
    raw = notifications if isinstance(notifications, dict) else {}
    compact = dict(raw)
    compact["items"] = list(raw.get("items") or [])[:_DASHBOARD_STATUS_NOTIFICATION_LIMIT]
    return compact


def _dashboard_live_run_payload(live_run: Any) -> dict[str, Any]:
    raw = live_run if isinstance(live_run, dict) else {}
    compact = dict(raw)
    compact["log"] = _dashboard_live_run_log_payload(raw.get("log"))
    compact["notifications"] = _dashboard_live_run_notifications_payload(raw.get("notifications"))
    return compact


def status_snapshot_for_scope(snapshot: dict[str, Any], *, scope: str = "full") -> dict[str, Any]:
    normalized_scope = str(scope or "full").strip().lower() or "full"
    if normalized_scope != "dashboard":
        return snapshot

    notifications = list(snapshot.get("notifications") or [])
    pr_queue = snapshot.get("pr_queue", snapshot.get("prQueue", {}))
    compact_snapshot: dict[str, Any] = {
        "ok": bool(snapshot.get("ok", False)),
        "repo": snapshot.get("repo", {}),
        "latest_run_dir": snapshot.get("latest_run_dir"),
        "snapshot_refresh": snapshot.get("snapshot_refresh", {}),
        "snapshotRefresh": snapshot.get("snapshotRefresh", snapshot.get("snapshot_refresh", {})),
        "active_run": snapshot.get("active_run", {}),
        "stages": list(snapshot.get("stages") or []),
        "backlog": snapshot.get("backlog", {}),
        "goals": _dashboard_goals_payload(snapshot.get("goals")),
        "logs": _dashboard_logs_payload(snapshot.get("logs")),
        "config": _dashboard_config_payload(snapshot.get("config"), snapshot.get("config_contract")),
        "config_contract": _dashboard_config_contract_payload(snapshot.get("config_contract")),
        "claude_advanced": snapshot.get("claude_advanced", {}),
        "claudeAdvanced": snapshot.get("claudeAdvanced", snapshot.get("claude_advanced", {})),
        "mcp_diagnostics": snapshot.get("mcp_diagnostics", {}),
        "mcpDiagnostics": snapshot.get("mcpDiagnostics", snapshot.get("mcp_diagnostics", {})),
        "todo": snapshot.get("todo", {}),
        "skills_status": snapshot.get("skills_status", {}),
        "skillsStatus": snapshot.get("skillsStatus", snapshot.get("skills_status", {})),
        "plugin_status": snapshot.get("plugin_status", {}),
        "pluginStatus": snapshot.get("pluginStatus", snapshot.get("plugin_status", {})),
        "enterprise_profile": snapshot.get("enterprise_profile", {}),
        "enterpriseProfile": snapshot.get("enterpriseProfile", snapshot.get("enterprise_profile", {})),
        "prompts": _dashboard_prompts_payload(snapshot.get("prompts")),
        "history": _dashboard_history_payload(snapshot.get("history")),
        "metrics": snapshot.get("metrics", {}),
        "notifications": notifications[:_DASHBOARD_STATUS_NOTIFICATION_LIMIT],
        "pr_queue": pr_queue,
        "prQueue": pr_queue,
        "worktree": snapshot.get("worktree", {}),
        "runner_control": snapshot.get("runner_control", {}),
        "web_instance": snapshot.get("web_instance", {}),
        "webInstance": snapshot.get("webInstance", snapshot.get("web_instance", {})),
        "liveRun": _dashboard_live_run_payload(snapshot.get("liveRun")),
        "redaction": snapshot.get("redaction", {}),
        "progress": snapshot.get("progress", {}),
        "execution_status": snapshot.get("execution_status", ""),
        "executionStatus": snapshot.get("executionStatus", snapshot.get("execution_status", "")),
        "project_complete": bool(snapshot.get("project_complete", False)),
        "projectComplete": bool(snapshot.get("projectComplete", snapshot.get("project_complete", False))),
        "project_status": snapshot.get("project_status", ""),
        "projectStatus": snapshot.get("projectStatus", snapshot.get("project_status", "")),
        "goals_complete": bool(snapshot.get("goals_complete", False)),
        "goalsComplete": bool(snapshot.get("goalsComplete", snapshot.get("goals_complete", False))),
        "backlog_complete": bool(snapshot.get("backlog_complete", False)),
        "backlogComplete": bool(snapshot.get("backlogComplete", snapshot.get("backlog_complete", False))),
        "sectionState": snapshot.get("sectionState", {}),
    }
    return compact_snapshot
