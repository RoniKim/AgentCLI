from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any

from .cli import DEFAULTS as CLI_DEFAULTS
from .prompts import PROMPT_SPECS, _read_text_robust, prompt_variables
from .utils import atomic_write_text


PROMPT_RESTORE_CONFIRMATION_PHRASE = "RESTORE BACKUP"
REDACTED_PROMPT_PREVIEW = "[redacted]"


def _pick_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _fmt_mtime(value: float) -> str:
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "template"


def _text_excerpt(text: str, *, max_lines: int = 8, max_chars: int = 280) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    lines = clean.splitlines()
    excerpt = "\n".join(lines[:max_lines]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3].rstrip() + "..."
    return excerpt


def _prompt_preview(text: str) -> str:
    preview = _text_excerpt(text, max_lines=6, max_chars=420)
    return preview or "(empty)"


def _prompt_summary(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    for line in lines:
        if not line.startswith("#"):
            return line[:180]
    return lines[0][:180]


def _prompt_variables(text: str) -> list[str]:
    return prompt_variables(text)


def _prompt_profile(cfg: dict[str, Any]) -> str:
    return _pick_text(cfg.get("profile"), CLI_DEFAULTS.get("profile"), "personal") or "personal"


def _prompt_spec_map() -> dict[str, dict[str, str]]:
    return {str(spec["id"]): spec for spec in PROMPT_SPECS}


def _prompt_template_dir(repo_root: Path) -> Path:
    return (repo_root / "templates" / "agent_prompts").resolve()


def _prompt_template_resolved_path(repo_root: Path, spec: dict[str, str]) -> Path | None:
    template_dir = _prompt_template_dir(repo_root)
    candidate = (template_dir / str(spec.get("file") or "")).expanduser()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(template_dir)
    except Exception:
        return None
    return resolved


def _prompt_default_text(repo_root: Path, spec: dict[str, str]) -> str:
    template_path = _prompt_template_resolved_path(repo_root, spec)
    if template_path is None:
        return spec["default"]
    if template_path.exists() and template_path.is_file():
        try:
            return _read_text_robust(template_path)
        except Exception:
            pass
    return spec["default"]


def _read_prompt_text(prompt_path: Path, default_text: str) -> tuple[str, bool]:
    if prompt_path.exists() and prompt_path.is_file():
        try:
            return _read_text_robust(prompt_path), True
        except Exception:
            return "", True
    return default_text, False


def _prompt_resolved_path(prompts_dir: Path, file_name: str) -> Path:
    return (prompts_dir / file_name).resolve()


def _prompt_file_name_is_bare(file_name: str) -> bool:
    candidate = str(file_name or "").strip().replace("\\", "/")
    if not candidate:
        return False
    if candidate in {".", ".."}:
        return False
    if "/" in candidate or ":" in candidate:
        return False
    return Path(candidate).name == candidate


def _prompt_backup_path(prompt_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return prompt_path.with_name(f"{prompt_path.stem}.{stamp}.bak{prompt_path.suffix}")


def _prompt_backup_candidates(prompt_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    pattern = f"{prompt_path.stem}.*.bak{prompt_path.suffix}"
    try:
        parent = prompt_path.parent
        if parent.exists() and parent.is_dir():
            for candidate in sorted(
                [path for path in parent.glob(pattern) if path.is_file()],
                key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
                reverse=True,
            )[: max(0, int(limit)) or 0]:
                try:
                    stats = candidate.stat()
                except Exception:
                    continue
                candidates.append(
                    {
                        "path": candidate.as_posix(),
                        "name": candidate.name,
                        "updated": _fmt_mtime(stats.st_mtime),
                        "size": stats.st_size,
                        "summary": f"{_fmt_mtime(stats.st_mtime)} | {stats.st_size} bytes",
                    }
                )
    except Exception:
        return []
    return candidates


def _prompt_validation_payload(
    *,
    file_name: str,
    expected_file: str,
    content: str,
    required_variables: list[str],
) -> dict[str, Any]:
    draft_file = str(file_name or "").strip()
    required = [str(name) for name in required_variables if str(name).strip()]
    draft_variables = _prompt_variables(content)
    missing_variables = [name for name in required if name not in draft_variables]
    file_error = ""
    file_error_code = ""
    if not draft_file:
        file_error = "Filename cannot be empty."
        file_error_code = "prompt_file_required"
    elif not _prompt_file_name_is_bare(draft_file):
        file_error = "Filename must be a bare filename within the resolved prompts directory."
        file_error_code = "prompt_file_invalid"
    elif expected_file and draft_file != expected_file:
        file_error = f"Filename must stay {expected_file}."
        file_error_code = "prompt_file_mismatch"
    content_error = "" if str(content or "").strip() else "Prompt content cannot be empty."
    content_error_code = "prompt_content_required" if content_error else ""
    template_error = ""
    if missing_variables:
        template_error = f"Missing template variables: {', '.join(f'{{{name}}}' for name in missing_variables)}"
    template_error_code = "prompt_template_variables_missing" if template_error else ""
    errors: list[dict[str, str]] = []
    if file_error:
        errors.append(
            {
                "field": "file",
                "code": file_error_code or "prompt_file_required",
                "message": file_error,
            }
        )
    if content_error:
        errors.append(
            {
                "field": "content",
                "code": "prompt_content_required",
                "message": content_error,
            }
        )
    if template_error:
        errors.append(
            {
                "field": "content",
                "code": "prompt_template_variables_missing",
                "message": template_error,
            }
        )
    return {
        "ok": not errors,
        "file_error": file_error,
        "file_error_code": file_error_code,
        "content_error": content_error,
        "content_error_code": content_error_code,
        "template_error": template_error,
        "template_error_code": template_error_code,
        "required_variables": required,
        "draft_variables": draft_variables,
        "missing_variables": missing_variables,
        "errors": errors,
    }


def _prompt_shared_payload(
    spec: dict[str, str],
    prompts_dir: Path,
    repo_root: Path,
    *,
    profile: str,
) -> tuple[dict[str, Any], str, bool, Path, list[str], list[str]]:
    prompt_path = _prompt_resolved_path(prompts_dir, spec["file"])
    default_text = _prompt_default_text(repo_root, spec)
    content, exists = _read_prompt_text(prompt_path, default_text)
    mode = "override" if exists else "template"
    source = prompts_dir.as_posix() if exists else "templates/agent_prompts"
    updated = _fmt_mtime(prompt_path.stat().st_mtime) if exists else "template"
    variables = _prompt_variables(content)
    required_variables = _prompt_variables(default_text)
    payload = {
        "id": spec["id"],
        "file": spec["file"],
        "path": prompt_path.as_posix(),
        "scope": spec["scope"],
        "profile": profile,
        "source": source,
        "mode": mode,
        "updated": updated,
        "content_length": len(content or ""),
        "template_variables": variables,
        "required_template_variables": required_variables,
    }
    return payload, content, exists, prompt_path, variables, required_variables


def _prompt_inventory_item(
    spec: dict[str, str],
    prompts_dir: Path,
    repo_root: Path,
    *,
    profile: str,
) -> dict[str, Any]:
    payload, content, _, _, _, _ = _prompt_shared_payload(spec, prompts_dir, repo_root, profile=profile)
    payload.update(
        {
            "summary": f"{profile} profile | {str(payload['mode']).title()} prompt available ({len(content or '')} characters).",
            "preview": REDACTED_PROMPT_PREVIEW,
        }
    )
    return payload


def _prompt_read_payload(
    spec: dict[str, str],
    prompts_dir: Path,
    repo_root: Path,
    *,
    profile: str,
) -> dict[str, Any]:
    payload, content, exists, prompt_path, _, required_variables = _prompt_shared_payload(spec, prompts_dir, repo_root, profile=profile)
    payload.update(
        {
            "ok": True,
            "exists": exists,
            "content": content,
            "preview": _prompt_preview(content),
            "summary": _prompt_summary(content),
            "validation": _prompt_validation_payload(
                file_name=spec["file"],
                expected_file=spec["file"],
                content=content,
                required_variables=required_variables,
            ),
            "backups": _prompt_backup_candidates(prompt_path),
        }
    )
    return payload


def _load_prompt_items(repo_root: Path, prompts_dir: Path, *, profile: str) -> list[dict[str, Any]]:
    return [_prompt_inventory_item(spec, prompts_dir, repo_root, profile=profile) for spec in PROMPT_SPECS]


def _prompt_error_payload(status_code: int, code: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status_code": status_code,
        "code": code,
        "message": message,
        "details": details,
    }
    return payload


def resolve_prompt_target(
    prompt_dir: Path,
    prompt_id: str,
    prompt_file: str,
) -> tuple[dict[str, str] | None, Path | None, dict[str, Any] | None]:
    spec = _prompt_spec_map().get(prompt_id)
    if spec is None:
        return None, None, _prompt_error_payload(404, "prompt_not_found", "The requested prompt id was not found.", id=prompt_id)

    expected_rel = Path(spec["file"]).as_posix()
    requested_file = str(prompt_file or "").strip()
    candidate = Path(requested_file.replace("\\", "/")).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (prompt_dir / candidate).resolve()

    try:
        resolved.relative_to(prompt_dir)
    except Exception:
        return (
            spec,
            None,
            _prompt_error_payload(
                400,
                "prompt_path_outside_prompts_dir",
                "Prompt file must stay within the resolved prompts directory.",
                path=resolved.as_posix(),
                prompts_dir=prompt_dir.as_posix(),
            ),
        )

    resolved_rel = resolved.relative_to(prompt_dir).as_posix()
    if resolved_rel != expected_rel:
        return (
            spec,
            None,
            _prompt_error_payload(
                400,
                "prompt_file_mismatch",
                "The requested prompt file does not match the prompt id.",
                expected=expected_rel,
                actual=resolved_rel,
            ),
        )

    if not _prompt_file_name_is_bare(requested_file):
        return (
            spec,
            None,
            _prompt_error_payload(
                400,
                "prompt_file_invalid",
                "Prompt file must be a bare filename within the resolved prompts directory.",
                file=requested_file,
                expected=expected_rel,
            ),
        )

    return spec, resolved, None


def save_prompt(
    *,
    repo_root: Path,
    prompt_dir: Path,
    profile: str,
    prompt_id: str,
    prompt_file: str,
    content: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    backup_path: Path | None = None
    try:
        spec, prompt_path, error = resolve_prompt_target(prompt_dir, prompt_id, prompt_file)
        if error is not None or spec is None or prompt_path is None:
            return None, error or _prompt_error_payload(404, "prompt_not_found", "The requested prompt id was not found.", id=prompt_id)

        if not isinstance(content, str):
            content = str(content)

        required_variables = _prompt_variables(_prompt_default_text(repo_root, spec))
        validation = _prompt_validation_payload(
            file_name=str(prompt_file).strip(),
            expected_file=spec["file"],
            content=content,
            required_variables=required_variables,
        )
        if not validation["ok"]:
            first_error = validation["errors"][0] if validation["errors"] else {"code": "prompt_validation_failed", "message": "Prompt validation failed."}
            return (
                None,
                _prompt_error_payload(
                    400,
                    str(first_error.get("code") or "prompt_validation_failed"),
                    str(first_error.get("message") or "Prompt validation failed."),
                    path=prompt_path.as_posix(),
                    validation=validation,
                ),
            )

        current_content, current_exists = _read_prompt_text(prompt_path, _prompt_default_text(repo_root, spec))
        backup_path = _prompt_backup_path(prompt_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if current_exists:
            shutil.copy2(prompt_path, backup_path)
        else:
            atomic_write_text(backup_path, current_content)

        atomic_write_text(prompt_path, content)
        saved_prompt = _prompt_read_payload(spec, prompt_dir, repo_root, profile=profile)
        return (
            {
                "ok": True,
                "action": "prompt-save",
                "status": "saved",
                "message": f"Prompt saved. Backup written to {backup_path.as_posix()}.",
                "prompt": saved_prompt,
                "backup_path": backup_path.as_posix(),
                "saved_path": prompt_path.as_posix(),
            },
            None,
        )
    except Exception as ex:
        details: dict[str, Any] = {"path": ""}
        if backup_path is not None:
            details["backup_path"] = backup_path.as_posix()
        return None, _prompt_error_payload(500, "prompt_save_failed", f"Prompt save failed: {ex}", **details)


def restore_prompt(
    *,
    repo_root: Path,
    prompt_dir: Path,
    profile: str,
    prompt_id: str,
    prompt_file: str,
    restore_path_value: str,
    confirmation: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    backup_path: Path | None = None
    try:
        spec, prompt_path, error = resolve_prompt_target(prompt_dir, prompt_id, prompt_file)
        if error is not None or spec is None or prompt_path is None:
            return None, error or _prompt_error_payload(404, "prompt_not_found", "The requested prompt id was not found.", id=prompt_id)

        if not confirmation:
            return (
                None,
                _prompt_error_payload(
                    400,
                    "prompt_restore_confirmation_required",
                    "A restore confirmation phrase is required.",
                    expected=PROMPT_RESTORE_CONFIRMATION_PHRASE,
                ),
            )
        if confirmation != PROMPT_RESTORE_CONFIRMATION_PHRASE:
            return (
                None,
                _prompt_error_payload(
                    400,
                    "prompt_restore_confirmation_mismatch",
                    "The restore confirmation phrase did not match.",
                    expected=PROMPT_RESTORE_CONFIRMATION_PHRASE,
                ),
            )

        candidate = Path(str(restore_path_value).strip().replace("\\", "/")).expanduser()
        restored_from = candidate.resolve() if candidate.is_absolute() else (prompt_dir / candidate).resolve()
        try:
            restored_from.relative_to(prompt_dir)
        except Exception:
            return (
                None,
                _prompt_error_payload(
                    400,
                    "prompt_backup_path_outside_prompts_dir",
                    "Backup path must stay within the resolved prompts directory.",
                    path=restored_from.as_posix(),
                    prompts_dir=prompt_dir.as_posix(),
                ),
            )

        if not restored_from.exists() or not restored_from.is_file():
            return (
                None,
                _prompt_error_payload(
                    404,
                    "prompt_backup_not_found",
                    "The selected backup file was not found.",
                    path=restored_from.as_posix(),
                ),
            )

        backup_pattern = f"{prompt_path.stem}.*.bak{prompt_path.suffix}"
        if (
            restored_from.parent != prompt_path.parent
            or not restored_from.name.startswith(f"{prompt_path.stem}.")
            or not restored_from.name.endswith(f".bak{prompt_path.suffix}")
            or restored_from.name not in {path.name for path in prompt_path.parent.glob(backup_pattern)}
        ):
            return (
                None,
                _prompt_error_payload(
                    400,
                    "prompt_backup_not_found",
                    "The selected backup file is not available for this prompt.",
                    path=restored_from.as_posix(),
                ),
            )

        current_content, current_exists = _read_prompt_text(prompt_path, _prompt_default_text(repo_root, spec))
        backup_path = _prompt_backup_path(prompt_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if current_exists:
            shutil.copy2(prompt_path, backup_path)
        else:
            atomic_write_text(backup_path, current_content)

        restore_text = _read_text_robust(restored_from)
        atomic_write_text(prompt_path, restore_text)
        restored_prompt = _prompt_read_payload(spec, prompt_dir, repo_root, profile=profile)
        return (
            {
                "ok": True,
                "action": "prompt-restore",
                "status": "restored",
                "message": f"Prompt restored from {restored_from.as_posix()}. Backup written to {backup_path.as_posix()}.",
                "prompt": restored_prompt,
                "backup_path": backup_path.as_posix(),
                "restored_from_path": restored_from.as_posix(),
                "saved_path": prompt_path.as_posix(),
            },
            None,
        )
    except Exception as ex:
        details: dict[str, Any] = {"path": ""}
        if backup_path is not None:
            details["backup_path"] = backup_path.as_posix()
        return None, _prompt_error_payload(500, "prompt_restore_failed", f"Prompt restore failed: {ex}", **details)
