from __future__ import annotations

import json
import sys
from pathlib import Path

from .active_goal import (
    build_active_goal_analytics,
    build_active_goal_status,
    build_active_goal_timeline,
    cancel_active_goal,
    clear_active_goal,
    complete_active_goal,
    create_active_goal,
    import_active_goal_state,
    list_active_goal_autonomy_presets,
    list_active_goal_templates,
    recommend_next_active_goals,
    update_active_goal,
    write_active_goal_export,
)
from .cli import parse_args
from .runner_entry import run


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _handle_active_goal_command(args: object) -> int | None:
    repo = Path(str(getattr(args, "repo", "") or ".")).expanduser().resolve()
    run_now = bool(getattr(args, "run_now", False))
    expected_etag = str(getattr(args, "active_goal_etag", "") or "")

    if bool(getattr(args, "active_goal_templates", False)):
        _print_json(list_active_goal_templates())
        return None if run_now else 0

    if bool(getattr(args, "active_goal_presets", False)):
        _print_json(list_active_goal_autonomy_presets())
        return None if run_now else 0

    if bool(getattr(args, "active_goal_recommend", False)):
        _print_json(recommend_next_active_goals(repo))
        return None if run_now else 0

    if bool(getattr(args, "active_goal_timeline", False)):
        _print_json(build_active_goal_timeline(repo))
        return None if run_now else 0

    if bool(getattr(args, "active_goal_analytics", False)):
        _print_json(build_active_goal_analytics(repo))
        return None if run_now else 0

    if bool(getattr(args, "active_goal_export", False)):
        _print_json(write_active_goal_export(repo))
        return None if run_now else 0

    import_path = str(getattr(args, "active_goal_import", "") or "").strip()
    if import_path:
        payload = json.loads(Path(import_path).expanduser().read_text(encoding="utf-8-sig", errors="replace"))
        _print_json(
            import_active_goal_state(
                repo,
                payload if isinstance(payload, dict) else {},
                replace=bool(getattr(args, "active_goal_replace", False)),
                expected_etag=expected_etag,
            )
        )
        return None if run_now else 0

    if bool(getattr(args, "active_goal_clear", False)):
        _print_json(clear_active_goal(repo, expected_etag=expected_etag))
        return 0

    complete_text = getattr(args, "active_goal_complete", None)
    if complete_text is not None:
        _print_json(complete_active_goal(repo, evidence=str(complete_text or ""), expected_etag=expected_etag))
        return 0

    cancel_text = getattr(args, "active_goal_cancel", None)
    if cancel_text is not None:
        _print_json(cancel_active_goal(repo, reason=str(cancel_text or ""), expected_etag=expected_etag))
        return 0

    if bool(getattr(args, "active_goal_update", False)):
        update_kwargs: dict[str, object] = {"expected_etag": expected_etag}
        if getattr(args, "active_goal_objective", None) is not None:
            update_kwargs["objective"] = str(getattr(args, "active_goal_objective") or "")
        if getattr(args, "active_goal_mode", None) is not None:
            update_kwargs["mode"] = str(getattr(args, "active_goal_mode") or "")
        if getattr(args, "active_goal_template", None) is not None:
            update_kwargs["template_key"] = str(getattr(args, "active_goal_template") or "")
        if getattr(args, "active_goal_preset", None) is not None:
            update_kwargs["autonomy_preset_key"] = str(getattr(args, "active_goal_preset") or "")
        if getattr(args, "active_goal_token_budget", None) is not None:
            update_kwargs["token_budget"] = int(getattr(args, "active_goal_token_budget") or 0)
        if getattr(args, "active_goal_time_budget_seconds", None) is not None:
            update_kwargs["time_budget_seconds"] = int(getattr(args, "active_goal_time_budget_seconds") or 0)
        if getattr(args, "active_goal_cycle_budget", None) is not None:
            update_kwargs["cycle_budget"] = int(getattr(args, "active_goal_cycle_budget") or 0)
        if getattr(args, "active_goal_notes", None) is not None:
            update_kwargs["notes"] = str(getattr(args, "active_goal_notes") or "")
        if set(update_kwargs) == {"expected_etag"}:
            _print_json({"ok": False, "error": {"code": "active_goal_update_empty", "message": "Active goal update has no changes."}})
            return 2
        _print_json(update_active_goal(repo, **update_kwargs))
        return None if run_now else 0

    objective = getattr(args, "active_goal_objective", None)
    if objective is not None:
        status = create_active_goal(
            repo,
            str(objective or ""),
            mode=str(getattr(args, "active_goal_mode", "") or "adaptive"),
            token_budget=int(getattr(args, "active_goal_token_budget", 0) or 0),
            time_budget_seconds=int(getattr(args, "active_goal_time_budget_seconds", 0) or 0),
            cycle_budget=int(getattr(args, "active_goal_cycle_budget", 0) or 0),
            template_key=str(getattr(args, "active_goal_template", "") or ""),
            autonomy_preset_key=str(getattr(args, "active_goal_preset", "") or ""),
            replace=bool(getattr(args, "active_goal_replace", False)),
            expected_etag=expected_etag,
            source={"kind": "operator", "surface": "cli"},
        )
        _print_json(status)
        return None if run_now else 0

    if bool(getattr(args, "active_goal_status", False)):
        _print_json(build_active_goal_status(repo))
        return None if run_now else 0

    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    active_goal_rc = _handle_active_goal_command(args)
    if active_goal_rc is not None:
        return active_goal_rc
    return run(args)
