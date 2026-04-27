"""Process Guard: 4-layer defense against orphan child processes.

L1 - Windows Job Object (KILL_ON_JOB_CLOSE): OS-level automatic cleanup on parent exit.
L2 - PID tracking + atexit: Graceful cleanup on normal exit or unhandled exceptions.
L3 - Enhanced signal handlers: Kill children on SIGINT/SIGTERM/SIGBREAK.
L4 - Startup orphan cleanup: Detect and kill orphans from previous runs.

Thread-safety:
    All mutable module state is protected by ``_lock`` (a re-entrant lock) so that
    signal handlers can safely call ``terminate_all_children`` even while a normal
    code-path holds the lock on the same thread.

Job Object handle lifecycle:
    ``_job_handle`` is intentionally kept open for the process lifetime.
    ``KILL_ON_JOB_CLOSE`` fires when the *last* handle to the Job Object is closed,
    which happens automatically when our process exits.  Closing it earlier would
    kill children prematurely.

_is_claude_process:
    This function spawns ``tasklist`` and must NEVER be called from signal handlers
    or atexit handlers.  It is only used by ``cleanup_orphans`` (L4, startup-only).
"""
from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module state (protected by _lock — RLock for signal-handler re-entrancy)
# ---------------------------------------------------------------------------
_lock = threading.RLock()
_initialized = False
_tracked_pids: set[int] = set()
_session_dir: Optional[Path] = None
_stop_path_func: Optional[Callable[[], Optional[Path]]] = None
_job_handle: Optional[int] = None

# Session files older than this (seconds) are unconditionally cleaned up,
# regardless of PID liveness — guards against PID-recycling false positives.
_SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# ---------------------------------------------------------------------------
# L1 - Windows Job Object
# ---------------------------------------------------------------------------

# Win32 constants
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260

# WaitForSingleObject return values
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102

# ctypes type alias
_HANDLE = ctypes.c_void_p


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * _MAX_PATH),
    ]


def _init_kernel32_types() -> None:
    """Set Win32 API function signatures once (idempotent, call under _lock)."""
    if sys.platform != "win32":
        return
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    kernel32.CreateJobObjectW.restype = _HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.SetInformationJobObject.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    kernel32.GetCurrentProcess.restype = _HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [_HANDLE]
    kernel32.GetLastError.restype = ctypes.c_uint32
    kernel32.GetLastError.argtypes = []
    kernel32.OpenProcess.restype = _HANDLE
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [_HANDLE, ctypes.c_uint]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [_HANDLE, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = _HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32FirstW.argtypes = [_HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [_HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]


def _setup_job_object() -> Optional[int]:
    """Create a Windows Job Object with KILL_ON_JOB_CLOSE and assign current process."""
    if sys.platform != "win32":
        return None
    job = None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            logger.warning("[ProcessGuard] Failed to create Job Object")
            return None

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        ok = kernel32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            logger.warning("[ProcessGuard] Failed to set Job Object limits")
            kernel32.CloseHandle(job)
            return None

        current_process = kernel32.GetCurrentProcess()
        ok = kernel32.AssignProcessToJobObject(job, current_process)
        if not ok:
            err = kernel32.GetLastError()
            if err == 5:
                logger.warning("[ProcessGuard] Process already in a Job Object, L1 skipped")
            else:
                logger.warning(f"[ProcessGuard] Failed to assign to Job Object (err={err})")
            kernel32.CloseHandle(job)
            return None

        logger.info("[ProcessGuard] L1: Job Object created (KILL_ON_JOB_CLOSE)")
        return job
    except Exception as ex:
        logger.warning(f"[ProcessGuard] L1 setup failed: {ex}")
        try:
            if job:
                kernel32.CloseHandle(job)
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# L2 - PID tracking + atexit
# ---------------------------------------------------------------------------

def _resolve_session_dir(requested: Optional[Path]) -> Path:
    """Resolve and create session directory with fallback for read-only FS."""
    candidates = []
    if requested is not None:
        candidates.append(requested)
    candidates.append(Path.home() / ".agentcli" / "sessions")
    candidates.append(Path(tempfile.gettempdir()) / "agentcli_sessions")

    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            # Verify writable by creating a temp file
            probe = d / ".probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return d
        except Exception:
            continue
    # Last resort — may not be writable, but at least return something
    return candidates[0]


def _get_session_dir() -> Path:
    """Return the session directory, creating it if needed."""
    if _session_dir is not None:
        return _session_dir
    return _resolve_session_dir(None)


def _session_file(pid: int) -> Path:
    return _get_session_dir() / f"session_{pid}.json"


def register_pid(pid: int) -> None:
    """Register a child process PID for tracking (thread-safe)."""
    with _lock:
        _tracked_pids.add(pid)
    try:
        data = {
            "child_pid": pid,
            "parent_pid": os.getpid(),
            "created_at": time.time(),
        }
        sf = _session_file(pid)
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(data), encoding="utf-8")
    except Exception as ex:
        logger.debug(f"[ProcessGuard] Session file write failed for PID {pid}: {ex}")
    logger.debug(f"[ProcessGuard] Registered child PID {pid}")


def unregister_pid(pid: int) -> None:
    """Unregister a child process PID (thread-safe)."""
    with _lock:
        _tracked_pids.discard(pid)
    try:
        sf = _session_file(pid)
        if sf.exists():
            sf.unlink()
    except Exception as ex:
        logger.debug(f"[ProcessGuard] Session file cleanup failed for PID {pid}: {ex}")
    logger.debug(f"[ProcessGuard] Unregistered child PID {pid}")


def unregister_pid_if_exited(pid: int) -> bool:
    """Unregister PID only after it is no longer alive."""
    if _pid_alive(pid):
        logger.warning(f"[ProcessGuard] PID {pid} still appears alive; keeping session file")
        return False
    unregister_pid(pid)
    return True


def _kill_pid(pid: int) -> bool:
    """Kill a single process by PID. On Windows always uses TerminateProcess."""
    if pid <= 0 or pid == os.getpid():
        return True
    try:
        if sys.platform == "win32":
            _init_kernel32_types()
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if handle:
                ok = bool(kernel32.TerminateProcess(handle, 1))
                kernel32.CloseHandle(handle)
                return ok or not _pid_alive(pid)
            return not _pid_alive(pid)
        else:
            os.kill(pid, signal.SIGTERM)
            return True
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _windows_child_pid_map() -> dict[int, list[int]]:
    """Return parent PID -> child PIDs on Windows without spawning helper tools."""
    if sys.platform != "win32":
        return {}
    try:
        _init_kernel32_types()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        invalid = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid:
            return {}

        child_map: dict[int, list[int]] = {}
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        try:
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                pid = int(entry.th32ProcessID)
                parent_pid = int(entry.th32ParentProcessID)
                child_map.setdefault(parent_pid, []).append(pid)
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return child_map
    except Exception:
        return {}


def _descendant_pids(root_pid: int, child_map: dict[int, list[int]]) -> list[int]:
    """Return all descendants of root_pid using a parent->children map."""
    descendants: list[int] = []
    seen: set[int] = set()
    stack = list(child_map.get(root_pid, []))
    my_pid = os.getpid()
    while stack:
        pid = int(stack.pop())
        if pid in seen or pid <= 0 or pid == my_pid:
            continue
        seen.add(pid)
        descendants.append(pid)
        stack.extend(child_map.get(pid, []))
    return descendants


def terminate_process_tree(pid: int, *, include_root: bool = True) -> None:
    """Terminate a managed process and any remaining descendants."""
    if pid <= 0 or pid == os.getpid():
        return
    if sys.platform == "win32":
        for child_pid in reversed(_descendant_pids(pid, _windows_child_pid_map())):
            _kill_pid(child_pid)
    if include_root:
        _kill_pid(pid)


def process_descendant_pids(pid: int) -> list[int]:
    """Return currently visible descendants for a process PID."""
    if pid <= 0:
        return []
    if sys.platform == "win32":
        return _descendant_pids(pid, _windows_child_pid_map())
    return []


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    try:
        if sys.platform == "win32":
            _init_kernel32_types()
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                try:
                    return int(kernel32.GetLastError()) == 5
                except Exception:
                    return False
            result = kernel32.WaitForSingleObject(handle, 0)
            kernel32.CloseHandle(handle)
            return result == _WAIT_TIMEOUT
        else:
            os.kill(pid, 0)
            return True
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False


def _summarize_pids(pids: list[int], *, limit: int = 40) -> str:
    if len(pids) <= limit:
        return str(pids)
    head = ", ".join(str(pid) for pid in pids[:limit])
    return f"[{head}, ...] ({len(pids)} total)"


def _terminate_windows_pids_bulk(pids: list[int]) -> None:
    """Terminate tracked Windows PIDs using a single process snapshot."""
    child_map = _windows_child_pid_map()
    seen: set[int] = set()
    kill_order: list[int] = []
    my_pid = os.getpid()

    def _add(pid: int) -> None:
        if pid <= 0 or pid == my_pid or pid in seen:
            return
        seen.add(pid)
        kill_order.append(pid)

    for pid in pids:
        if pid <= 0 or pid == my_pid:
            continue
        for child_pid in reversed(_descendant_pids(pid, child_map)):
            _add(child_pid)
        _add(pid)

    for pid in kill_order:
        _kill_pid(pid)


def _wait_for_pids_exit(
    pids: list[int],
    *,
    timeout_seconds: float = 3.0,
    interval_seconds: float = 0.05,
) -> None:
    """Wait briefly for killed processes to become signaled before cleanup."""
    pending = {pid for pid in pids if pid > 0 and pid != os.getpid()}
    if not pending:
        return
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while pending:
        pending = {pid for pid in pending if _pid_alive(pid)}
        if not pending or time.monotonic() >= deadline:
            return
        time.sleep(max(0.01, interval_seconds))


def _terminate_pids(pids: list[int], *, wait: bool = True) -> None:
    """Kill a list of PIDs. If wait=True on Unix, SIGTERM → wait → SIGKILL."""
    seen: set[int] = set()
    unique_pids: list[int] = []
    for pid in pids:
        if pid not in seen:
            seen.add(pid)
            unique_pids.append(pid)

    if sys.platform == "win32":
        _terminate_windows_pids_bulk(unique_pids)
    else:
        for pid in unique_pids:
            _kill_pid(pid)

    if wait:
        if sys.platform == "win32":
            _wait_for_pids_exit(unique_pids)
        else:
            time.sleep(0.5)
            for pid in unique_pids:
                if _pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass

    # Cleanup tracked set and session files
    for pid in unique_pids:
        unregister_pid_if_exited(pid)


def terminate_all_children(*, _from_signal: bool = False) -> None:
    """Terminate all tracked child processes.

    Args:
        _from_signal: If True, called from signal handler — skip sleep/wait
                      to avoid blocking. Safe because RLock allows re-entry
                      from the same thread.
    """
    with _lock:
        pids = list(_tracked_pids)
    if not pids:
        return
    logger.info(
        "[ProcessGuard] Terminating %d tracked child process(es): %s",
        len(pids),
        _summarize_pids(pids),
    )
    _terminate_pids(pids, wait=not _from_signal)


def _atexit_handler() -> None:
    """atexit handler: kill all tracked children on interpreter shutdown."""
    with _lock:
        has_pids = bool(_tracked_pids)
    if has_pids:
        logger.info("[ProcessGuard] L2: atexit cleanup triggered")
        terminate_all_children(_from_signal=False)


# ---------------------------------------------------------------------------
# L3 - Enhanced signal handlers
# ---------------------------------------------------------------------------

def _make_signal_handler(
    stop_path_func: Optional[Callable[[], Optional[Path]]],
) -> Callable[[int, object], None]:
    """Create a signal handler that writes STOP file + kills children.

    The handler is kept minimal and avoids sleeping.  STOP file is written via
    low-level ``os.open``/``os.write`` to minimise interaction with Python's
    higher-level I/O machinery.
    """

    def _handler(signum: int, frame: object) -> None:
        # Write STOP file using low-level I/O (safer in signal context)
        # Directory is pre-created in install_signal_handlers(); avoid mkdir here.
        if stop_path_func is not None:
            try:
                stop_path = stop_path_func()
                if stop_path is not None:
                    content = f"signal {signum}\n".encode("utf-8")
                    fd = os.open(str(stop_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                    try:
                        os.write(fd, content)
                    finally:
                        os.close(fd)
            except Exception:
                pass

        # Kill tracked children immediately (no wait — signal handler context)
        terminate_all_children(_from_signal=True)

        try:
            sys.stderr.write(
                f"[ProcessGuard] L3: Signal {signum} received, children terminated.\n"
            )
            sys.stderr.flush()
        except Exception:
            pass

    return _handler


def install_signal_handlers(
    stop_path_func: Optional[Callable[[], Optional[Path]]] = None,
) -> None:
    """Install enhanced signal handlers (L3)."""
    func = stop_path_func or _stop_path_func
    # Pre-create the stop file directory so the signal handler doesn't need mkdir
    if func is not None:
        try:
            sp = func()
            if sp is not None:
                sp.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    handler = _make_signal_handler(func)
    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        pass
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, handler)  # type: ignore[attr-defined]
        except (ValueError, OSError):
            pass
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, handler)  # type: ignore[attr-defined]
        except (ValueError, OSError):
            pass
    logger.debug("[ProcessGuard] L3: Signal handlers installed")


# ---------------------------------------------------------------------------
# L4 - Startup orphan cleanup
# ---------------------------------------------------------------------------

def _is_managed_child_process(pid: int) -> bool:
    """Check if PID still looks like a process type AgentCLI launched.

    WARNING: This spawns ``tasklist`` — must NEVER be called from signal
    handlers or atexit handlers.
    """
    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
            )
            output = result.stdout.lower()
            # Exact image-name match: tasklist CSV gives "image_name.exe","PID",...
            # Keep startup orphan cleanup conservative.  Shell/runtime names
            # such as cmd.exe, powershell.exe, and python.exe are too generic:
            # a stale session file plus PID reuse could otherwise terminate an
            # unrelated interactive terminal or Python process.
            for token in output.split(","):
                name = token.strip().strip('"')
                if name in (
                    "node.exe",
                    "node",
                    "codex.exe",
                    "codex",
                    "claude.exe",
                    "claude",
                ):
                    return True
            return False
        else:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text(errors="replace").lower()
                return any(name in cmdline for name in ("node", "claude", "codex", "python"))
    except Exception:
        pass
    return False


def _is_claude_process(pid: int) -> bool:
    """Backward-compatible alias for older tests/imports."""
    return _is_managed_child_process(pid)


def cleanup_orphans(session_dir: Optional[Path] = None) -> int:
    """Scan session files and kill orphaned child processes (L4).

    Returns the number of orphans killed.
    """
    sd = session_dir or _get_session_dir()
    if not sd.exists():
        return 0

    killed = 0
    my_pid = os.getpid()
    now = time.time()
    file_count = 0

    for sf in list(sd.glob("session_*.json")):
        file_count += 1
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            child_pid = data.get("child_pid")
            parent_pid = data.get("parent_pid")
            created_at = data.get("created_at", 0)

            if child_pid is None or parent_pid is None:
                sf.unlink(missing_ok=True)
                continue

            # Skip our own session files — they belong to the current process
            if parent_pid == my_pid:
                continue

            # TTL guard: unconditionally remove very old session files to prevent
            # accumulation and PID-recycling false positives.
            if now - created_at > _SESSION_TTL_SECONDS:
                logger.debug(
                    f"[ProcessGuard] L4: TTL expired for session {sf.name} "
                    f"(age={int(now - created_at)}s), removing"
                )
                sf.unlink(missing_ok=True)
                continue

            parent_alive = _pid_alive(parent_pid)
            child_alive = _pid_alive(child_pid)

            if not parent_alive:
                # Parent is dead — this is a stale session file
                if child_alive and _is_managed_child_process(child_pid):
                    logger.warning(
                        f"[ProcessGuard] L4: Killing orphan PID {child_pid} "
                        f"(parent {parent_pid} dead)"
                    )
                    terminate_process_tree(child_pid, include_root=True)
                    killed += 1
                sf.unlink(missing_ok=True)
            elif not child_alive:
                # Parent alive but child already exited — stale file
                sf.unlink(missing_ok=True)
            # else: both alive — leave session file alone (active instance)
        except Exception as ex:
            logger.debug(f"[ProcessGuard] L4: Error processing {sf}: {ex}")
            try:
                sf.unlink(missing_ok=True)
            except Exception:
                pass

    if file_count:
        logger.debug(f"[ProcessGuard] L4: Scanned {file_count} session file(s)")
    if killed:
        logger.info(f"[ProcessGuard] L4: Cleaned up {killed} orphan process(es)")
    return killed


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_process_guard(
    session_dir: Optional[Path] = None,
    stop_path_func: Optional[Callable[[], Optional[Path]]] = None,
) -> None:
    """Initialize all process guard layers (idempotent, thread-safe).

    Args:
        session_dir: Directory for session PID files. Defaults to ~/.agentcli/sessions.
        stop_path_func: Callable returning the current STOP file path for signal handlers.
    """
    global _initialized, _session_dir, _stop_path_func, _job_handle

    with _lock:
        if _initialized:
            # Update stop_path_func if provided (runner may set it later)
            if stop_path_func is not None:
                _stop_path_func = stop_path_func
                try:
                    install_signal_handlers(stop_path_func)
                except ValueError:
                    logger.debug("[ProcessGuard] L3: Signal handlers skipped (not main thread)")
            return

        _session_dir = _resolve_session_dir(session_dir)
        _stop_path_func = stop_path_func

        # Set Win32 API types once
        _init_kernel32_types()

        # L1: Job Object (handle intentionally kept open — see module docstring)
        _job_handle = _setup_job_object()

        # L2: atexit
        atexit.register(_atexit_handler)
        logger.debug("[ProcessGuard] L2: atexit handler registered")

        # L3: signal handlers (best effort, may fail in non-main thread)
        try:
            install_signal_handlers(stop_path_func)
        except ValueError:
            logger.debug("[ProcessGuard] L3: Signal handlers skipped (not main thread)")

        # L4: cleanup orphans from previous runs
        try:
            cleanup_orphans(_session_dir)
        except Exception as ex:
            logger.debug(f"[ProcessGuard] L4: Orphan cleanup failed: {ex}")

        _initialized = True

    logger.info(
        "[ProcessGuard] Initialized (L1=%s, L2=atexit, L3=signals, L4=orphan-scan)",
        "JobObject" if _job_handle else "skipped",
    )
