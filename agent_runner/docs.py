from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Optional


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
