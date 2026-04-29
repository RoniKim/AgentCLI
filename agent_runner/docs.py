from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Optional, Sequence

from .cli import DEFAULTS as CLI_DEFAULTS, _build_parser
from .gitops import WORKTREE_MERGE_PENDING, WORKTREE_MERGE_PENDING_MD
from .runtime_contract import CODEX_MODEL_DEFAULTS
from .utils import _KNOWN_STOP_REASONS


def resolve_docs_dir(repo: Path, docs_dir_arg: str) -> Optional[Path]:
    p = Path(docs_dir_arg).expanduser()
    cand = p if p.is_absolute() else (repo / p)
    if cand.exists() and cand.is_dir():
        return cand.resolve()
    for fb in [repo / ".doc" / "Docs", repo / ".doc" / "docs", repo / "Docs", repo / "docs"]:
        if fb.exists() and fb.is_dir():
            return fb.resolve()
    return None


def read_text_robust(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    for enc in ("cp949", "euc-kr"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def extract_headings(md: str, max_headings: int = 80) -> list[str]:
    out: list[str] = []
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        lvl = len(s) - len(s.lstrip("#"))
        if 1 <= lvl <= 3:
            out.append(s)
            if len(out) >= max_headings:
                break
    return out


def _repo_relative_path(repo: Path, path: Path) -> str:
    repo_resolved = repo.resolve()
    try:
        return path.resolve().relative_to(repo_resolved).as_posix()
    except Exception:
        return path.as_posix()


def list_md_files(docs_dir: Path) -> list[Path]:
    if not docs_dir.exists() or not docs_dir.is_dir():
        return []
    return sorted(
        [p for p in docs_dir.rglob("*.md") if p.is_file()],
        key=lambda x: x.relative_to(docs_dir).as_posix().casefold(),
    )


def build_docs_digest_text(repo: Path, docs_dir: Path) -> str:
    docs = list_md_files(docs_dir)
    docs_dir_rel = _repo_relative_path(repo, docs_dir)

    lines: list[str] = []
    lines.append("# DOCS DIGEST")
    lines.append("")
    lines.append("This digest is a compact index for AgentCLI PM/Dev/QA runs.")
    lines.append(f"It is generated from the current `{docs_dir_rel}` file inventory.")
    lines.append("")
    lines.append("## Inventory")
    if docs:
        for f in docs:
            rel = _repo_relative_path(repo, f)
            lines.append(f"- {rel}")
    else:
        lines.append("- (no markdown files found)")
    lines.append("")
    for f in docs:
        text, used = read_text_robust(f)
        rel = _repo_relative_path(repo, f)
        lines.append(f"## {rel}")
        lines.append(f"- path: `{rel}`")
        lines.append(f"- decoded_as: `{used}`")
        heads = extract_headings(text)
        if heads:
            lines.append("- headings:")
            for h in heads:
                lines.append(f"  - {h}")
        else:
            lines.append("- headings: (none found)")
        lines.append("")
    return "\n".join(lines)


def generate_docs_digest(repo: Path, docs_dir: Path, digest_path: Path) -> None:
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(build_docs_digest_text(repo, docs_dir), encoding="utf-8", errors="strict", newline="\n")


def validate_docs_digest(repo: Path, docs_dir: Path, digest_path: Path) -> list[str]:
    if not docs_dir.exists() or not docs_dir.is_dir():
        return [f"docs directory does not exist: {docs_dir.as_posix()}"]
    if not digest_path.exists() or not digest_path.is_file():
        return [f"digest file does not exist: {digest_path.as_posix()}"]
    expected = build_docs_digest_text(repo, docs_dir)
    actual, _ = read_text_robust(digest_path)
    if actual != expected:
        return [
            f"digest file is stale: {digest_path.as_posix()}",
            f"regenerate from current inventory: { _repo_relative_path(repo, docs_dir) }",
        ]
    return []


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")


def _looks_like_path(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if "://" in stripped:
        return False
    if stripped.startswith("@"):
        return False
    if stripped in {"OK", "HIGH", "MEDIUM", "LOW"}:
        return False
    if stripped.startswith("/") or stripped.startswith("."):
        return True
    if "/" in stripped or "\\" in stripped:
        return True
    suffix = Path(stripped).suffix.lower()
    return suffix in {".md", ".py", ".html", ".js", ".jsx", ".json", ".txt", ".yaml", ".yml", ".css", ".bat", ".sh"}


def _extract_path_claims_from_cell(cell: str) -> list[str]:
    claims: list[str] = []
    for match in _MARKDOWN_LINK_RE.finditer(cell):
        target = match.group(2).strip()
        if _looks_like_path(target):
            claims.append(target)
    for match in _CODE_SPAN_RE.finditer(cell):
        target = match.group(1).strip()
        if _looks_like_path(target):
            claims.append(target)
    if not claims:
        stripped = cell.strip()
        if _looks_like_path(stripped):
            claims.append(stripped)
    deduped: list[str] = []
    for claim in claims:
        if claim not in deduped:
            deduped.append(claim)
    return deduped


def _section_base_from_heading(heading: str) -> Path | None:
    if ".doc/Docs/incidents/" in heading:
        return Path(".doc/Docs/incidents")
    if ".doc/Docs/" in heading:
        return Path(".doc/Docs")
    if "docs/" in heading:
        return Path("docs")
    return None


def _normalize_claim_path(claim: str) -> str:
    normalized = claim.strip().strip("`").strip()
    normalized = normalized.split("?", 1)[0].split("#", 1)[0]
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _resolve_claim_relpath(claim: str, base: Path) -> Path:
    normalized = _normalize_claim_path(claim)
    if normalized.startswith(
        (
            "docs/",
            ".doc/",
            "agent_runner/",
            "web_console/",
            "tests/",
            "templates/",
            "prompts/",
            "scripts/",
            ".AgentCLI/",
            "README.md",
            "GOALS.md",
            "CLAUDE.md",
            "Agent.md",
            "SKILLS_ENHANCEMENT_PLAN.md",
            "agent_cli.py",
            "start_web.bat",
        )
    ):
        return Path(normalized)
    return base / normalized


def _resolve_case_sensitive_path(repo: Path, rel_path: Path) -> tuple[Path | None, str | None]:
    if rel_path.is_absolute():
        return None, f"absolute path claims are not allowed: {rel_path.as_posix()}"

    repo_resolved = repo.resolve()
    current = repo_resolved
    parts = [part for part in rel_path.parts if part not in ("", ".")]
    for part in parts:
        if part == "..":
            return None, f"path escapes repo root: {rel_path.as_posix()}"
        if not current.exists():
            return None, f"missing parent path while resolving {rel_path.as_posix()}"
        if not current.is_dir():
            return None, f"path crosses a file before completion: {current.relative_to(repo_resolved).as_posix()}"

        children = list(current.iterdir())
        exact = next((child for child in children if child.name == part), None)
        if exact is not None:
            current = exact
            continue

        ci = next((child for child in children if child.name.lower() == part.lower()), None)
        if ci is not None:
            return None, (
                f"case mismatch: {rel_path.as_posix()} -> {ci.relative_to(repo_resolved).as_posix()}"
            )
        return None, f"missing path: {rel_path.as_posix()}"

    return current, None


def validate_master_index(repo: Path, index_path: Path) -> list[str]:
    if not index_path.exists() or not index_path.is_file():
        return [f"master index does not exist: {index_path.as_posix()}"]

    text, _ = read_text_robust(index_path)
    errors: list[str] = []
    current_base: Path | None = None
    in_code_block = False

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        base = _section_base_from_heading(stripped)
        if base is not None:
            current_base = base
            continue

        if current_base is None or not stripped.startswith("|") or set(stripped) <= {"|", "-"}:
            continue

        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue

        for claim in _extract_path_claims_from_cell(cells[0]):
            resolved_rel = _resolve_claim_relpath(claim, current_base)
            _, error = _resolve_case_sensitive_path(repo, resolved_rel)
            if error is not None:
                errors.append(f"{index_path.as_posix()}:{line_no}: {error} (claim: {claim})")

    return errors


def _merge_route_methods(route_map: dict[str, set[str]], path: str, methods: set[str]) -> None:
    if not path:
        return
    route_map.setdefault(path, set()).update(methods)


def _route_methods_from_app_route(route: Any) -> set[str]:
    path = str(getattr(route, "path", "") or "").strip()
    if not path:
        return set()
    methods = {
        str(method).upper()
        for method in getattr(route, "methods", set()) or set()
        if str(method).upper() not in {"HEAD", "OPTIONS"}
    }
    return methods


def _collect_route_inventory_from_app(app: Any) -> dict[str, tuple[str, ...]]:
    route_map: dict[str, set[str]] = {}
    for route in getattr(app, "routes", []) or []:
        path = str(getattr(route, "path", "") or "").strip()
        if not path:
            continue
        methods = _route_methods_from_app_route(route)
        if methods:
            _merge_route_methods(route_map, path, methods)
    return {path: tuple(sorted(methods)) for path, methods in sorted(route_map.items())}


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_string_set(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    items: list[str] = []
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            value = _literal_string(elt)
            if value is not None:
                items.append(value)
    else:
        value = _literal_string(node)
        if value is not None:
            items.append(value)
    return {item.upper() for item in items if item}


def _route_decorator_path_and_methods(decorator: ast.expr) -> tuple[str | None, set[str]]:
    if not isinstance(decorator, ast.Call):
        return None, set()
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None, set()
    if not isinstance(func.value, ast.Name) or func.value.id != "app":
        return None, set()

    path = _literal_string(decorator.args[0]) if decorator.args else None
    if not path:
        return None, set()

    attr = func.attr
    if attr in {"get", "post", "put", "patch", "delete", "head", "options"}:
        return path, {attr.upper()}
    if attr == "api_route":
        methods = set()
        for kw in decorator.keywords:
            if kw.arg == "methods":
                methods = _literal_string_set(kw.value)
                break
        return path, methods
    return None, set()


def _collect_route_inventory_from_source(source_path: Path) -> dict[str, tuple[str, ...]]:
    if not source_path.exists() or not source_path.is_file():
        return {}

    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=source_path.as_posix())
    route_map: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            path, methods = _route_decorator_path_and_methods(decorator)
            if path and methods:
                _merge_route_methods(route_map, path, methods)

    return {path: tuple(sorted(methods)) for path, methods in sorted(route_map.items())}


def collect_fastapi_route_inventory(repo: Path | None = None) -> dict[str, tuple[str, ...]]:
    source_path = Path(__file__).resolve().with_name("web.py")
    route_inventory = _collect_route_inventory_from_source(source_path)
    if route_inventory:
        return route_inventory

    try:
        from .web import create_app
    except Exception:
        return route_inventory

    try:
        app = create_app(repo)
        return _collect_route_inventory_from_app(app)
    except Exception:
        return route_inventory


def validate_web_console_route_claims(text: str, route_inventory: dict[str, tuple[str, ...]]) -> list[str]:
    errors: list[str] = []
    claims: list[tuple[str | None, str]] = []

    for match in re.finditer(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/api/[^\s`]+)", text):
        claims.append((match.group(1).upper(), match.group(2)))

    for match in re.finditer(r"(?<!\w)(/api/[^\s`]+)", text):
        claims.append((None, match.group(1)))

    seen: set[tuple[str | None, str]] = set()
    for method, raw_path in claims:
        path = raw_path.split("?", 1)[0].split("#", 1)[0].rstrip(".,;:)\"]'")
        if "*" in path:
            continue
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)

        methods = route_inventory.get(path)
        if methods is None:
            errors.append(f"missing FastAPI route: {path}")
            continue
        if method is not None and method not in methods:
            errors.append(
                f"method mismatch for {path}: claimed {method}, actual {', '.join(methods)}"
            )

    return errors


def _section_text(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    start: int | None = None
    heading_level: int | None = None
    in_code_block = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if stripped == heading:
            start = idx + 1
            heading_level = len(stripped) - len(stripped.lstrip("#"))
            break

    if start is None or heading_level is None:
        return None

    end = len(lines)
    in_code_block = False
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if stripped.startswith("#"):
            current_level = len(stripped) - len(stripped.lstrip("#"))
            if current_level <= heading_level:
                end = idx
                break

    return "\n".join(lines[start:end]).strip()


def _markdown_table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        if all(not cell or set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _normalize_doc_scalar(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        text = text[1:-1].strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        text = text[1:-1].strip()
    return text


def _row_code_spans(cell: str) -> list[str]:
    return [span.strip() for span in _CODE_SPAN_RE.findall(cell or "") if span.strip()]


def _table_value_map(text: str) -> dict[str, str]:
    rows = _markdown_table_rows(text)
    out: dict[str, str] = {}
    for cells in rows:
        if len(cells) < 2:
            continue
        key_spans = _row_code_spans(cells[0])
        if not key_spans:
            continue
        key = _normalize_doc_scalar(key_spans[0] if key_spans else cells[0])
        if not key:
            continue
        value_spans = _row_code_spans(cells[1])
        value = _normalize_doc_scalar(value_spans[0] if value_spans else cells[1])
        out[key] = value
    return out


def _table_first_cell_values(text: str) -> dict[str, list[str]]:
    rows = _markdown_table_rows(text)
    out: dict[str, list[str]] = {}
    for cells in rows:
        if not cells:
            continue
        key_spans = _row_code_spans(cells[0])
        if key_spans:
            out[key_spans[0]] = key_spans
    return out


def _extract_json_block(text: str) -> str:
    match = re.search(r"```json\s*\n(.*?)\n```", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def collect_runner_cli_flags() -> set[str]:
    parser = _build_parser()
    return {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", []) or []
        if option.startswith("--")
    }


def collect_web_cli_flags() -> set[str]:
    source_path = Path(__file__).resolve().with_name("web.py")
    if not source_path.exists() or not source_path.is_file():
        return set()
    text = source_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=source_path.as_posix())
    except Exception:
        return set()
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        for arg in node.args:
            value = _literal_string(arg)
            if value and value.startswith("--"):
                flags.add(value)
    return flags


def collect_telegram_notify_events() -> set[str]:
    from .remote.telegram_service import _NOTIFY_EVENT_ALLOWED

    return set(_NOTIFY_EVENT_ALLOWED)


def collect_stop_reason_values() -> set[str]:
    return set(_KNOWN_STOP_REASONS)


def _validate_required_sections(text: str, doc_label: str, headings: Sequence[str]) -> list[str]:
    errors: list[str] = []
    for heading in headings:
        if _section_text(text, heading) is None:
            errors.append(f"{doc_label}: missing required section: {heading}")
    return errors


def _validate_exact_table_map(
    section_text: str,
    expected: dict[str, str],
    *,
    doc_label: str,
    section_label: str,
) -> list[str]:
    errors: list[str] = []
    actual = _table_value_map(section_text)
    for key, expected_value in expected.items():
        if key not in actual:
            errors.append(f"{doc_label}: missing {section_label} row: {key}")
            continue
        if actual[key] != expected_value:
            errors.append(
                f"{doc_label}: stale {section_label} value for {key}: expected {expected_value!r}, found {actual[key]!r}"
            )
    for key in sorted(set(actual) - set(expected)):
        errors.append(f"{doc_label}: stale {section_label} row name: {key}")
    return errors


def _validate_flag_table(
    section_text: str,
    allowed_flags: set[str],
    *,
    doc_label: str,
    section_label: str,
    required_flags: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    actual_flags: set[str] = set()
    for cells in _markdown_table_rows(section_text):
        if not cells:
            continue
        for flag in _row_code_spans(cells[0]):
            if flag.startswith("--"):
                actual_flags.add(flag)
                if flag not in allowed_flags:
                    errors.append(f"{doc_label}: stale {section_label} flag: {flag}")
    if required_flags is not None:
        missing = required_flags - actual_flags
        for flag in sorted(missing):
            errors.append(f"{doc_label}: missing {section_label} flag: {flag}")
    return errors


def _validate_web_operating_model_section(
    section_text: str,
    *,
    doc_label: str,
    section_label: str,
) -> list[str]:
    errors: list[str] = []
    lowered = section_text.lower()

    if "one repo, one web instance" not in lowered:
        errors.append(f"{doc_label}: {section_label} must describe the one repo, one web instance model")
    if "active repo" not in lowered or "startup" not in lowered:
        errors.append(
            f"{doc_label}: {section_label} must explain that active repo identity is the startup-bound repository"
        )
    if "repo-level instance lock" not in lowered:
        errors.append(f"{doc_label}: {section_label} must mention the repo-level instance lock")
    if "multi-repo" not in lowered:
        errors.append(f"{doc_label}: {section_label} must mention deferred multi-repo dashboard scope")
    elif not any(marker in lowered for marker in ("deferred", "later phase", "future scope")):
        errors.append(
            f"{doc_label}: {section_label} must explicitly defer multi-repo dashboard scope to a later phase"
        )

    return errors


def _validate_exact_first_cell_set(
    section_text: str,
    expected_values: set[str],
    *,
    doc_label: str,
    section_label: str,
) -> list[str]:
    errors: list[str] = []
    actual_values = set(_table_first_cell_values(section_text))
    for value in sorted(actual_values - expected_values):
        errors.append(f"{doc_label}: stale {section_label} value: {value}")
    for value in sorted(expected_values - actual_values):
        errors.append(f"{doc_label}: missing {section_label} value: {value}")
    return errors


def validate_configuration_doc(text: str) -> list[str]:
    doc_label = "docs/CONFIGURATION.md"
    errors = _validate_required_sections(
        text,
        doc_label,
        [
            "# 설정(Config) 관리",
            "# 실행 엔진(Backend) 선택",
            "# 역할별 모델 설정",
            "# Claude 백엔드 고급 설정",
        ],
    )

    codex_section = _section_text(text, "## Codex 백엔드 (GPT 모델 — Codex 크레딧으로 실행)")
    if codex_section is None:
        errors.append(f"{doc_label}: missing required section: ## Codex 백엔드 (GPT 모델 — Codex 크레딧으로 실행)")
    else:
        errors.extend(
            _validate_exact_table_map(
                codex_section,
                CODEX_MODEL_DEFAULTS,
                doc_label=doc_label,
                section_label="Codex model defaults",
            )
        )

    claude_section = _section_text(text, "## Claude 백엔드 (Claude 모델)")
    if claude_section is None:
        errors.append(f"{doc_label}: missing required section: ## Claude 백엔드 (Claude 모델)")
    else:
        expected = {
            "claudecode_model": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_model"])),
            "claudecode_pm_model": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_pm_model"])),
            "claudecode_dev_model": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_dev_model"])),
            "claudecode_dev_model_tier1": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_dev_model_tier1"])),
            "claudecode_dev_model_tier2": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_dev_model_tier2"])),
            "claudecode_qa_model": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_qa_model"])),
            "claudecode_reporter_model": _normalize_doc_scalar(str(CLI_DEFAULTS["claudecode_reporter_model"])),
        }
        errors.extend(
            _validate_exact_table_map(
                claude_section,
                expected,
                doc_label=doc_label,
                section_label="Claude model defaults",
            )
        )

    thinking_section = _section_text(text, "## Extended Thinking (확장 사고)")
    if thinking_section is None:
        errors.append(f"{doc_label}: missing required section: ## Extended Thinking (확장 사고)")
    else:
        payload = _extract_json_block(thinking_section)
        if not payload:
            errors.append(f"{doc_label}: missing JSON block for extended thinking defaults")
        else:
            try:
                data = json.loads(payload)
            except Exception as ex:
                errors.append(f"{doc_label}: invalid JSON in extended thinking defaults: {ex}")
            else:
                expected = {"claudecode_max_thinking_tokens": CLI_DEFAULTS["claudecode_max_thinking_tokens"]}
                for key, expected_value in expected.items():
                    actual_value = data.get(key)
                    if actual_value != expected_value:
                        errors.append(
                            f"{doc_label}: stale extended thinking default for {key}: expected {expected_value!r}, found {actual_value!r}"
                        )
                extra_keys = set(data) - set(expected)
                for key in sorted(extra_keys):
                    errors.append(f"{doc_label}: stale extended thinking key: {key}")

    session_section = _section_text(text, "## 세션 관리")
    if session_section is None:
        errors.append(f"{doc_label}: missing required section: ## 세션 관리")
    else:
        payload = _extract_json_block(session_section)
        if not payload:
            errors.append(f"{doc_label}: missing JSON block for session defaults")
        else:
            try:
                data = json.loads(payload)
            except Exception as ex:
                errors.append(f"{doc_label}: invalid JSON in session defaults: {ex}")
            else:
                expected = {
                    "claudecode_user": CLI_DEFAULTS["claudecode_user"],
                    "claudecode_fork_session": CLI_DEFAULTS["claudecode_fork_session"],
                    "claudecode_include_partial_messages": CLI_DEFAULTS["claudecode_include_partial_messages"],
                    "claudecode_setting_sources": CLI_DEFAULTS["claudecode_setting_sources"],
                }
                for key, expected_value in expected.items():
                    actual_value = data.get(key)
                    if actual_value != expected_value:
                        errors.append(
                            f"{doc_label}: stale session default for {key}: expected {expected_value!r}, found {actual_value!r}"
                        )
                extra_keys = set(data) - set(expected)
                for key in sorted(extra_keys):
                    errors.append(f"{doc_label}: stale session key: {key}")

    checkpoint_section = _section_text(text, "## 파일 체크포인팅 (Beta)")
    if checkpoint_section is None:
        errors.append(f"{doc_label}: missing required section: ## 파일 체크포인팅 (Beta)")
    else:
        payload = _extract_json_block(checkpoint_section)
        if not payload:
            errors.append(f"{doc_label}: missing JSON block for file checkpointing defaults")
        else:
            try:
                data = json.loads(payload)
            except Exception as ex:
                errors.append(f"{doc_label}: invalid JSON in file checkpointing defaults: {ex}")
            else:
                expected = {"claudecode_enable_file_checkpointing": CLI_DEFAULTS["claudecode_enable_file_checkpointing"]}
                for key, expected_value in expected.items():
                    actual_value = data.get(key)
                    if actual_value != expected_value:
                        errors.append(
                            f"{doc_label}: stale file checkpointing default for {key}: expected {expected_value!r}, found {actual_value!r}"
                        )
                extra_keys = set(data) - set(expected)
                for key in sorted(extra_keys):
                    errors.append(f"{doc_label}: stale file checkpointing key: {key}")

    return errors


def validate_operations_doc(text: str) -> list[str]:
    doc_label = "docs/OPERATIONS.md"
    errors = _validate_required_sections(
        text,
        doc_label,
        [
            "# 안전/운영 옵션 (Git, Stop, No-diff)",
            "## Stop file로 안전 종료",
            "## Web Operating Model",
            "## CLI flags",
            "## Stop reason reference",
            "## Worktree 격리 모드 (권장: 안전하게 오래 돌릴 때)",
            "# 예산 가드레일 (Budget Guardrails)",
            "# 빌드/테스트 게이트",
            "# 정책/시크릿 스캔(옵션)",
        ],
    )

    stop_section = _section_text(text, "## Stop file로 안전 종료")
    if stop_section is not None:
        timeout_rows = _markdown_table_rows(stop_section)
        timeout_row = next((cells for cells in timeout_rows if cells and _normalize_doc_scalar(cells[0]) == "stop_wait_timeout_seconds"), None)
        if timeout_row is None:
            errors.append(f"{doc_label}: missing stop_wait_timeout_seconds row")
        else:
            value_cell = timeout_row[1] if len(timeout_row) > 1 else ""
            value = _row_code_spans(value_cell)[0] if _row_code_spans(value_cell) else _normalize_doc_scalar(value_cell)
            expected = str(CLI_DEFAULTS["stop_wait_timeout_seconds"])
            if value != expected:
                errors.append(
                    f"{doc_label}: stale stop_wait_timeout_seconds value: expected {expected!r}, found {value!r}"
                )

    web_model_section = _section_text(text, "## Web Operating Model")
    if web_model_section is not None:
        errors.extend(
            _validate_web_operating_model_section(
                web_model_section,
                doc_label=doc_label,
                section_label="web operating model",
            )
        )

    cli_section = _section_text(text, "## CLI flags")
    if cli_section is not None:
        required_flags = {
            "--repo",
            "--config",
            "--run-now",
            "--non-interactive",
            "--autopilot",
            "--no-autopilot",
            "--continuous",
            "--no-continuous",
            "--loop",
            "--no-loop",
            "--loop-sleep-seconds",
            "--loop-max-cycles",
            "--loop-idle-exit-after",
            "--iterations",
            "--max-turns-per-task",
            "--stop-file",
            "--stop-wait-timeout-seconds",
            "--allow-no-diff",
            "--no-allow-no-diff",
            "--no-build",
            "--build",
            "--run-tests",
            "--no-run-tests",
            "--worktree-isolation",
            "--no-worktree-isolation",
            "--dangerous-git-rollback",
            "--no-dangerous-git-rollback",
            "--dotnet-build-target",
            "--dotnet-test-target",
            "--dotnet-test-filter",
        }
        errors.extend(
            _validate_flag_table(
                cli_section,
                collect_runner_cli_flags(),
                doc_label=doc_label,
                section_label="CLI",
                required_flags=required_flags,
            )
        )

    budget_section = _section_text(text, "# 예산 가드레일 (Budget Guardrails)")
    if budget_section is not None:
        table = _table_value_map(budget_section)
        expected = {
            key: str(value)
            for key, value in CLI_DEFAULTS["budgets"].items()
        }
        for key, expected_value in expected.items():
            actual_value = table.get(key)
            if actual_value is None:
                errors.append(f"{doc_label}: missing budget default row: {key}")
            elif actual_value != expected_value:
                errors.append(
                    f"{doc_label}: stale budget default for {key}: expected {expected_value!r}, found {actual_value!r}"
                )
        for key in sorted(set(table) - set(expected)):
            errors.append(f"{doc_label}: stale budget row name: {key}")

    reason_section = _section_text(text, "## Stop reason reference")
    if reason_section is not None:
        expected_reasons = collect_stop_reason_values()
        actual_reasons: set[str] = set()
        for cells in _markdown_table_rows(reason_section):
            if not cells:
                continue
            reason_spans = _row_code_spans(cells[0])
            if not reason_spans:
                continue
            reason = reason_spans[0]
            actual_reasons.add(reason)
            if reason not in expected_reasons:
                errors.append(f"{doc_label}: stale stop reason: {reason}")
            if reason == "project_complete":
                description = " ".join(cells[1:]).lower()
                if "goals_completion_level" not in description and "completion level" not in description:
                    errors.append(
                        f"{doc_label}: project_complete description must mention goals_completion_level or completion level"
                    )
        missing_reasons = expected_reasons - actual_reasons
        for reason in sorted(missing_reasons):
            errors.append(f"{doc_label}: missing stop reason row: {reason}")

    worktree_section = _section_text(text, "## Worktree 격리 모드 (권장: 안전하게 오래 돌릴 때)")
    if worktree_section is not None:
        expected_modes = {"manual", "auto"}
        actual_modes: set[str] = set()
        for cells in _markdown_table_rows(worktree_section):
            if not cells:
                continue
            mode_spans = _row_code_spans(cells[0])
            if not mode_spans:
                continue
            mode = mode_spans[0]
            actual_modes.add(mode)
            if mode not in expected_modes:
                errors.append(f"{doc_label}: stale worktree merge mode: {mode}")
        for mode in sorted(expected_modes - actual_modes):
            errors.append(f"{doc_label}: missing worktree merge mode row: {mode}")
        if WORKTREE_MERGE_PENDING not in worktree_section:
            errors.append(f"{doc_label}: worktree merge section must mention {WORKTREE_MERGE_PENDING}")
        if WORKTREE_MERGE_PENDING_MD not in worktree_section:
            errors.append(f"{doc_label}: worktree merge section must mention {WORKTREE_MERGE_PENDING_MD}")
        if "WORKTREE_APPLY_FAILURE.md" not in worktree_section:
            errors.append(f"{doc_label}: worktree merge section must mention WORKTREE_APPLY_FAILURE.md")
        if "/merge-worktree" not in worktree_section or "/discard-worktree" not in worktree_section:
            errors.append(f"{doc_label}: worktree merge section must mention /merge-worktree and /discard-worktree")
        if "clean" not in worktree_section.lower() or "hash" not in worktree_section.lower():
            errors.append(f"{doc_label}: worktree merge section must mention clean source repo and patch hash checks")

    shutdown_section = _section_text(text, "# 산출물(Artifacts) 구조")
    if shutdown_section is not None:
        required_tokens = {
            "write_run_report_artifacts": "run report writer",
            "build_local_shutdown_report": "local shutdown report builder",
            "write_emergency_shutdown_report": "emergency shutdown writer",
            "SHUTDOWN_REPORT.md": "shutdown report artifact",
            "SHUTDOWN_CONTEXT.json": "shutdown context artifact",
            "QA_VALIDATION_REPORT.json": "QA validation JSON artifact",
            "QA_VALIDATION_REPORT.md": "QA validation markdown artifact",
            "FINAL_RUN_REPORT.json": "final run JSON artifact",
            "FINAL_RUN_REPORT.md": "final run markdown artifact",
            "EMERGENCY_SHUTDOWN.md": "emergency shutdown artifact",
            "PM_SHUTDOWN_REPORT_OUTPUT.txt": "raw PM shutdown output artifact",
        }
        for token, label in required_tokens.items():
            if token not in shutdown_section:
                errors.append(f"{doc_label}: shutdown report section must mention {label}: {token}")
        lowered = shutdown_section.lower()
        if "best-effort" not in lowered and "best effort" not in lowered:
            errors.append(f"{doc_label}: shutdown report section must describe best-effort PM overwrite behavior")
        if "trim" not in lowered or "half" not in lowered:
            errors.append(f"{doc_label}: shutdown report section must mention duplicate half-content trimming")
        if "skip" not in lowered or "SHUTDOWN_REPORT.md" not in shutdown_section or "EMERGENCY_SHUTDOWN.md" not in shutdown_section:
            errors.append(f"{doc_label}: shutdown report section must explain emergency report skip behavior")

    return errors


def _validate_shutdown_report_section(
    section_text: str,
    *,
    doc_label: str,
    section_label: str,
    require_recovery_guidance: bool = False,
) -> list[str]:
    errors: list[str] = []
    required_tokens = {
        "write_run_report_artifacts": "run report writer",
        "build_local_shutdown_report": "local shutdown report builder",
        "write_emergency_shutdown_report": "emergency shutdown writer",
        "SHUTDOWN_REPORT.md": "shutdown report artifact",
        "SHUTDOWN_CONTEXT.json": "shutdown context artifact",
        "QA_VALIDATION_REPORT.json": "QA validation JSON artifact",
        "QA_VALIDATION_REPORT.md": "QA validation markdown artifact",
        "FINAL_RUN_REPORT.json": "final run JSON artifact",
        "FINAL_RUN_REPORT.md": "final run markdown artifact",
        "EMERGENCY_SHUTDOWN.md": "emergency shutdown artifact",
        "PM_SHUTDOWN_REPORT_OUTPUT.txt": "raw PM shutdown output artifact",
    }
    for token, label in required_tokens.items():
        if token not in section_text:
            errors.append(f"{doc_label}: {section_label} must mention {label}: {token}")

    lowered = section_text.lower()
    if "best-effort" not in lowered and "best effort" not in lowered:
        errors.append(f"{doc_label}: {section_label} must describe best-effort PM overwrite behavior")
    if "trim" not in lowered or "half" not in lowered:
        errors.append(f"{doc_label}: {section_label} must mention duplicate half-content trimming")
    if "skip" not in lowered or "SHUTDOWN_REPORT.md" not in section_text or "EMERGENCY_SHUTDOWN.md" not in section_text:
        errors.append(f"{doc_label}: {section_label} must explain emergency report skip behavior")
    if require_recovery_guidance:
        stale_phrases = [
            "유일한 회복 경로",
            "only recovery path",
            "reboot is the only",
            "재부팅이 유일한",
        ]
        if any(phrase in lowered for phrase in stale_phrases):
            errors.append(f"{doc_label}: {section_label} contains an obsolete reboot-only recovery claim")
    return errors


def validate_advanced_features_doc(text: str) -> list[str]:
    doc_label = "docs/ADVANCED_FEATURES.md"
    errors = _validate_required_sections(
        text,
        doc_label,
        [
            "# Shutdown Report 시스템",
            "## 보고서 생성 흐름",
            "## SHUTDOWN_CONTEXT 수집 항목",
            "## 비상 보고서 (Emergency)",
        ],
    )

    shutdown_section = _section_text(text, "# Shutdown Report 시스템")
    if shutdown_section is not None:
        errors.extend(
            _validate_shutdown_report_section(
                shutdown_section,
                doc_label=doc_label,
                section_label="shutdown report system",
            )
        )

    return errors


def validate_troubleshooting_doc(text: str) -> list[str]:
    doc_label = "docs/TROUBLESHOOTING.md"
    errors = _validate_required_sections(
        text,
        doc_label,
        [
            "# 트러블슈팅 (문제 상황 및 해결)",
            "## 23. Shutdown report / artifact recovery",
        ],
    )

    recovery_section = _section_text(text, "## 23. Shutdown report / artifact recovery")
    if recovery_section is not None:
        errors.extend(
            _validate_shutdown_report_section(
                recovery_section,
                doc_label=doc_label,
                section_label="shutdown report recovery",
                require_recovery_guidance=True,
            )
        )

    return errors


def validate_telegram_doc(text: str) -> list[str]:
    doc_label = "docs/TELEGRAM.md"
    errors = _validate_required_sections(
        text,
        doc_label,
        [
            "# Telegram 하이브리드 모드",
            "## 설정 키",
            "## CLI 오버라이드",
            "## 명령어",
            "## 푸시 알림",
            "### 이벤트 유형 레퍼런스",
            "## 여러 인스턴스",
        ],
    )

    settings_section = _section_text(text, "## 설정 키")
    if settings_section is not None:
        payload = _extract_json_block(settings_section)
        if not payload:
            errors.append(f"{doc_label}: missing Telegram config JSON block")
        else:
            try:
                data = json.loads(payload)
            except Exception as ex:
                errors.append(f"{doc_label}: invalid Telegram config JSON: {ex}")
            else:
                telegram = data.get("telegram")
                if not isinstance(telegram, dict):
                    errors.append(f"{doc_label}: Telegram config JSON must include a telegram object")
                else:
                    expected = dict(CLI_DEFAULTS["telegram"])
                    for key, expected_value in expected.items():
                        actual_value = telegram.get(key)
                        if actual_value != expected_value:
                            errors.append(
                                f"{doc_label}: stale Telegram default for {key}: expected {expected_value!r}, found {actual_value!r}"
                            )
                    extra_keys = set(telegram) - set(expected)
                    for key in sorted(extra_keys):
                        errors.append(f"{doc_label}: stale Telegram option key: {key}")
                    if telegram.get("instance_name") == "":
                        if "repo name" not in settings_section.lower() and "레포 이름" not in settings_section:
                            errors.append(
                                f"{doc_label}: Telegram settings section must note that blank instance_name falls back to the repo name"
                            )

    cli_section = _section_text(text, "## CLI 오버라이드")
    if cli_section is not None:
        required_flags = {
            "--telegram",
            "--telegram-runner-mode",
            "--telegram-poll-timeout",
            "--telegram-allowed-chat-id",
            "--telegram-bot-token",
            "--telegram-pairing-code",
            "--telegram-instance-name",
            "--telegram-notify-events",
            "--telegram-send-cycle-summary",
            "--no-telegram-send-cycle-summary",
            "--telegram-notify-interval",
            "--telegram-stalled-seconds",
        }
        errors.extend(
            _validate_flag_table(
                cli_section,
                collect_runner_cli_flags(),
                doc_label=doc_label,
                section_label="Telegram CLI",
                required_flags=required_flags,
            )
        )

    event_section = _section_text(text, "### 이벤트 유형 레퍼런스")
    if event_section is not None:
        actual_events: set[str] = set()
        default_events = set(CLI_DEFAULTS["telegram"]["notify_events"])
        allowed_events = collect_telegram_notify_events()
        for cells in _markdown_table_rows(event_section):
            if len(cells) < 4:
                continue
            event_spans = _row_code_spans(cells[0])
            if not event_spans:
                continue
            event = event_spans[0]
            actual_events.add(event)
            if event not in allowed_events:
                errors.append(f"{doc_label}: stale Telegram event name: {event}")
            active = _normalize_doc_scalar(cells[2])
            if event in default_events and active != "✅":
                errors.append(f"{doc_label}: default Telegram event must be marked active: {event}")
            if event not in default_events and active != "❌":
                errors.append(f"{doc_label}: optional Telegram event must be marked inactive: {event}")
            if event == "project_complete":
                description = _normalize_doc_scalar(cells[1]).lower()
                if "goals_completion_level" not in description and "completion level" not in description:
                    errors.append(
                        f"{doc_label}: project_complete description must mention goals_completion_level or completion level"
                    )
        missing_events = allowed_events - actual_events
        for event in sorted(missing_events):
            errors.append(f"{doc_label}: missing Telegram event row: {event}")

    return errors


def validate_web_console_doc(repo: Path, text: str) -> list[str]:
    doc_label = "docs/WEB_CONSOLE.md"
    errors = _validate_required_sections(
        text,
        doc_label,
        [
            "# AgentCLI Web Console",
            "## Operating Model",
            "## Current Status",
            "## Web Server Flags",
            "## Runner Controls",
            "## Validation",
            "## Worktree Diagnostics",
        ],
    )

    route_inventory = collect_fastapi_route_inventory(repo)
    errors.extend(validate_web_console_route_claims(text, route_inventory))

    operating_model_section = _section_text(text, "## Operating Model")
    if operating_model_section is not None:
        errors.extend(
            _validate_web_operating_model_section(
                operating_model_section,
                doc_label=doc_label,
                section_label="operating model",
            )
        )

    flags_section = _section_text(text, "## Web Server Flags")
    if flags_section is not None:
        required_flags = {
            "--repo",
            "--host",
            "--port",
            "--web-dir",
            "--config-path",
            "--enable-runner-controls",
            "--trusted-network",
        }
        errors.extend(
            _validate_flag_table(
                flags_section,
                collect_web_cli_flags(),
                doc_label=doc_label,
                section_label="web server",
                required_flags=required_flags,
            )
        )

    runner_controls_section = _section_text(text, "## Runner Controls")
    if runner_controls_section is not None:
        lowered = runner_controls_section.lower()
        if "agentcli_web_runner_controls" not in lowered:
            errors.append(
                f"{doc_label}: Runner Controls section must mention AGENTCLI_WEB_RUNNER_CONTROLS"
            )
        if "agentcli_web_trusted_network" not in lowered:
            errors.append(
                f"{doc_label}: Runner Controls section must mention AGENTCLI_WEB_TRUSTED_NETWORK"
            )

    status_section = _section_text(text, "## Current Status")
    if status_section is not None:
        summary_lines = [
            line
            for line in status_section.splitlines()
            if "no longer displayed as still running" in line.lower()
        ]
        if summary_lines:
            for line in summary_lines:
                code_values = _row_code_spans(line)
                invalid = sorted(value for value in code_values if value not in _KNOWN_STOP_REASONS)
                if invalid:
                    errors.append(
                        f"{doc_label}: stale final-reason claim in Current Status: {', '.join(invalid)}"
                    )

    return errors


def validate_user_facing_docs(repo: Path) -> list[str]:
    docs_root = repo / "docs"
    errors: list[str] = []

    configuration_path = docs_root / "CONFIGURATION.md"
    if configuration_path.exists():
        errors.extend(validate_configuration_doc(configuration_path.read_text(encoding="utf-8")))
    else:
        errors.append(f"missing docs file: {configuration_path.as_posix()}")

    operations_path = docs_root / "OPERATIONS.md"
    if operations_path.exists():
        errors.extend(validate_operations_doc(operations_path.read_text(encoding="utf-8")))
    else:
        errors.append(f"missing docs file: {operations_path.as_posix()}")

    advanced_features_path = docs_root / "ADVANCED_FEATURES.md"
    if advanced_features_path.exists():
        errors.extend(validate_advanced_features_doc(advanced_features_path.read_text(encoding="utf-8")))
    else:
        errors.append(f"missing docs file: {advanced_features_path.as_posix()}")

    troubleshooting_path = docs_root / "TROUBLESHOOTING.md"
    if troubleshooting_path.exists():
        errors.extend(validate_troubleshooting_doc(troubleshooting_path.read_text(encoding="utf-8")))
    else:
        errors.append(f"missing docs file: {troubleshooting_path.as_posix()}")

    telegram_path = docs_root / "TELEGRAM.md"
    if telegram_path.exists():
        errors.extend(validate_telegram_doc(telegram_path.read_text(encoding="utf-8")))
    else:
        errors.append(f"missing docs file: {telegram_path.as_posix()}")

    web_console_path = docs_root / "WEB_CONSOLE.md"
    if web_console_path.exists():
        errors.extend(validate_web_console_doc(repo, web_console_path.read_text(encoding="utf-8")))
    else:
        errors.append(f"missing docs file: {web_console_path.as_posix()}")

    return errors
