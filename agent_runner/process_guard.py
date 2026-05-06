"""Process Guard: layered defense against orphan child processes.

L1 - Windows Job Object (KILL_ON_JOB_CLOSE): OS-level automatic cleanup on parent exit.
L2 - PID tracking + atexit: Graceful cleanup on normal exit or unhandled exceptions.
L3 - Enhanced signal handlers: Kill children on SIGINT/SIGTERM/SIGBREAK.
L4 - Startup orphan cleanup: Detect and kill orphans from previous runs.
L5 - Parent watchdog: A detached helper cleans tracked children if the parent crashes.

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
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

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
_watchdog_process: Optional[subprocess.Popen[object]] = None

# Session files older than this (seconds) are unconditionally cleaned up,
# regardless of PID liveness — guards against PID-recycling false positives.
_SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_KILL_RETRY_BACKOFF_SECONDS = 15 * 60  # Avoid noisy retries for kernel-stuck processes.
# ---------------------------------------------------------------------------
# L1 - Windows Job Object
# ---------------------------------------------------------------------------

# Win32 constants
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260
_INFINITE = 0xFFFFFFFF

# Process creation flags
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008

# WaitForSingleObject return values
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102

# Parent watchdog polling is intentionally bounded so the helper never holds a
# single process handle open indefinitely.
_PARENT_WATCHDOG_POLL_SECONDS = 1.0
_WINDOWS_HANDLE_DIAGNOSTICS_GLOB = "windows-handle-diagnostics*.jsonl"
_WINDOWS_HANDLE_DIAGNOSTICS_ARTIFACT = "WINDOWS_HANDLE_DIAGNOSTICS.json"
_WINDOWS_HANDLE_DIAGNOSTICS_NORMALIZED_JSONL = "windows-handle-diagnostics.normalized.jsonl"

DEFAULT_WINDOWS_PROCESS_COUNT_WARNING_THRESHOLD = 25
DEFAULT_WINDOWS_HANDLE_COUNT_WARNING_THRESHOLD = 550_000
DEFAULT_WINDOWS_HANDLE_GROWTH_WARNING_THRESHOLD = 100

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


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
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
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [_HANDLE, ctypes.c_uint32]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [
        _HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
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
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )

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


def _filetime_to_int(ft: _FILETIME) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def _pid_create_time_ticks(pid: int) -> Optional[int]:
    """Return the Windows process creation FILETIME ticks for PID reuse checks."""
    if sys.platform != "win32" or pid <= 0:
        return None
    try:
        _init_kernel32_types()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        try:
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            return _filetime_to_int(creation)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _pid_executable_path(pid: int) -> Optional[str]:
    """Return the executable path for a live process PID when available."""
    if pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            _init_kernel32_types()
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                buffer = ctypes.create_unicode_buffer(32768)
                size = ctypes.c_uint32(len(buffer))
                ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
                if not ok:
                    return None
                path = str(buffer.value or "").strip()
                return Path(path).expanduser().as_posix() if path else None
            finally:
                kernel32.CloseHandle(handle)

        exe_path = os.readlink(f"/proc/{pid}/exe")
        exe_path = str(exe_path or "").strip()
        return Path(exe_path).expanduser().as_posix() if exe_path else None
    except Exception:
        return None


def _pid_signature_matches(pid: int, expected_ticks: object) -> bool:
    """Validate a PID still refers to the process recorded in a session file."""
    if expected_ticks in (None, ""):
        return True
    try:
        expected = int(expected_ticks)
    except Exception:
        return False
    actual = _pid_create_time_ticks(pid)
    return actual is not None and actual == expected


def register_pid(pid: int) -> None:
    """Register a child process PID for tracking (thread-safe)."""
    with _lock:
        _tracked_pids.add(pid)
    try:
        parent_pid = os.getpid()
        data = {
            "child_pid": pid,
            "parent_pid": parent_pid,
            "created_at": time.time(),
            "child_create_time": _pid_create_time_ticks(pid),
            "parent_create_time": _pid_create_time_ticks(parent_pid),
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


def tracked_pids(*, alive_only: bool = True) -> list[int]:
    """Return tracked child PIDs, optionally pruning exited ones."""
    with _lock:
        pids = sorted(_tracked_pids)
    if not alive_only:
        return pids
    live_pids: list[int] = []
    for pid in pids:
        if unregister_pid_if_exited(pid):
            live_pids.append(pid)
    return live_pids


def tracked_pid_details(*, alive_only: bool = True) -> list[dict[str, Any]]:
    """Return tracked child PID records with their session file paths."""
    with _lock:
        pids = sorted(_tracked_pids)
    records: list[dict[str, Any]] = []
    for pid in pids:
        alive = _pid_alive(pid)
        if alive_only and not alive:
            if unregister_pid_if_exited(pid):
                continue
        session_file = _session_file(pid)
        record = {
            "pid": pid,
            "alive": alive,
            "session_file": session_file.as_posix(),
            "session_exists": False,
        }
        try:
            record["session_exists"] = bool(session_file.exists())
        except Exception:
            pass
        records.append(record)
    return records


def process_guard_state() -> dict[str, Any]:
    """Return a read-only summary of the active process guard layers."""
    with _lock:
        session_dir = _session_dir
        stop_path_configured = _stop_path_func is not None
        initialized = bool(_initialized)
        job_object_active = bool(_job_handle)
        tracked_count = len(_tracked_pids)
    session_dir_text = session_dir.as_posix() if isinstance(session_dir, Path) else ""
    current_pid = os.getpid()
    return {
        "initialized": initialized,
        "session_dir": session_dir_text,
        "sessionDir": session_dir_text,
        "job_object_active": job_object_active,
        "jobObjectActive": job_object_active,
        "stop_path_configured": stop_path_configured,
        "stopPathConfigured": stop_path_configured,
        "tracked_pid_count": tracked_count,
        "trackedPidCount": tracked_count,
        "current_pid": current_pid,
        "currentPid": current_pid,
        "platform": sys.platform,
    }


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


def _process_tree_kill_order(pid: int, *, include_root: bool = True) -> list[int]:
    """Return descendants-first kill order for a managed process tree."""
    if pid <= 0 or pid == os.getpid():
        return []
    pids: list[int] = []
    if sys.platform == "win32":
        pids.extend(reversed(_descendant_pids(pid, _windows_child_pid_map())))
    if include_root:
        pids.append(pid)
    return [p for p in pids if p > 0 and p != os.getpid()]


def terminate_process_tree(
    pid: int,
    *,
    include_root: bool = True,
    wait: bool = False,
    timeout_seconds: float = 3.0,
) -> bool:
    """Terminate a managed process and any remaining descendants."""
    kill_order = _process_tree_kill_order(pid, include_root=include_root)
    if not kill_order:
        return True
    for target_pid in kill_order:
        _kill_pid(target_pid)
    if not wait:
        return True
    _wait_for_pids_exit(kill_order, timeout_seconds=timeout_seconds)
    return not any(_pid_alive(target_pid) for target_pid in kill_order)


def process_descendant_pids(pid: int) -> list[int]:
    """Return currently visible descendants for a process PID."""
    if pid <= 0:
        return []
    if sys.platform == "win32":
        return _descendant_pids(pid, _windows_child_pid_map())
    return []


def _watchdog_process_state(pid: int) -> tuple[bool, Optional[int]]:
    """Return a short-lived snapshot of a process' liveness and create-time."""
    if pid <= 0:
        return False, None
    if sys.platform == "win32":
        try:
            _init_kernel32_types()
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                try:
                    return int(kernel32.GetLastError()) == 5, None
                except Exception:
                    return False, None

            creation = _FILETIME()
            exit_time = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            try:
                alive = kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
                actual_create_time: Optional[int] = None
                try:
                    ok = kernel32.GetProcessTimes(
                        handle,
                        ctypes.byref(creation),
                        ctypes.byref(exit_time),
                        ctypes.byref(kernel),
                        ctypes.byref(user),
                    )
                    if ok:
                        actual_create_time = _filetime_to_int(creation)
                except Exception:
                    pass
                return alive, actual_create_time
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False, None

    alive = _pid_alive(pid)
    if not alive:
        return False, None
    return True, _pid_create_time_ticks(pid)


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


def _terminate_windows_pids_bulk(pids: list[int]) -> list[int]:
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
    return kill_order


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

    wait_pids = unique_pids
    if sys.platform == "win32":
        wait_pids = _terminate_windows_pids_bulk(unique_pids)
    else:
        for pid in unique_pids:
            _kill_pid(pid)

    if wait:
        if sys.platform == "win32":
            _wait_for_pids_exit(wait_pids)
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


def _session_allows_child_cleanup(data: dict[str, object], child_pid: int) -> bool:
    """Return True when a session file safely identifies the current child PID."""
    expected_child_time = data.get("child_create_time")
    if expected_child_time not in (None, ""):
        return _pid_signature_matches(child_pid, expected_child_time)
    # Backward compatibility for older unsigned session files. Keep conservative
    # process-name filtering because PID reuse cannot be ruled out.
    return _is_managed_child_process(child_pid)


def _session_parent_alive(data: dict[str, object], parent_pid: int) -> bool:
    """Return True only when the recorded parent still appears to be the same process."""
    if not _pid_alive(parent_pid):
        return False
    expected_parent_time = data.get("parent_create_time")
    if expected_parent_time not in (None, ""):
        actual = _pid_create_time_ticks(parent_pid)
        if actual is not None:
            try:
                return actual == int(expected_parent_time)
            except Exception:
                return False
    return True


def _cleanup_attempt_due(data: dict[str, object], now: float) -> bool:
    """Throttle repeated kill attempts for processes that remain stuck after termination."""
    if str(data.get("cleanup_status") or "") != "kill_pending":
        return True
    try:
        last = float(data.get("last_cleanup_attempt_at") or 0)
    except Exception:
        last = 0.0
    return (now - last) >= _KILL_RETRY_BACKOFF_SECONDS


def _mark_cleanup_pending(sf: Path, data: dict[str, object], message: str) -> None:
    payload = dict(data)
    attempts = payload.get("cleanup_attempts")
    try:
        attempt_count = int(attempts or 0) + 1
    except Exception:
        attempt_count = 1
    payload["cleanup_status"] = "kill_pending"
    payload["cleanup_message"] = message
    payload["last_cleanup_attempt_at"] = time.time()
    payload["cleanup_attempts"] = attempt_count
    try:
        sf.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cleanup_session_files_for_parent(session_dir: Path, parent_pid: int) -> int:
    """Kill signed child sessions for a parent that is known to be gone."""
    killed = 0
    my_pid = os.getpid()
    if not session_dir.exists():
        return 0
    for sf in list(session_dir.glob("session_*.json")):
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                sf.unlink(missing_ok=True)
                continue
            child_pid = data.get("child_pid")
            recorded_parent_pid = data.get("parent_pid")
            if child_pid is None or recorded_parent_pid is None:
                sf.unlink(missing_ok=True)
                continue
            try:
                child_pid_int = int(child_pid)
                recorded_parent_pid_int = int(recorded_parent_pid)
            except Exception:
                sf.unlink(missing_ok=True)
                continue
            if recorded_parent_pid_int != int(parent_pid):
                continue
            if child_pid_int == my_pid:
                continue
            if _pid_alive(child_pid_int) and _session_allows_child_cleanup(data, child_pid_int):
                logger.warning(
                    f"[ProcessGuard] L5: Killing child PID {child_pid_int} "
                    f"after parent {parent_pid} exited"
                )
                terminated = terminate_process_tree(child_pid_int, include_root=True, wait=True)
                if terminated or not _pid_alive(child_pid_int):
                    killed += 1
                    sf.unlink(missing_ok=True)
                else:
                    _mark_cleanup_pending(sf, data, "kill attempted but process remained alive")
                continue
            sf.unlink(missing_ok=True)
        except Exception as ex:
            logger.debug(f"[ProcessGuard] L5: Error processing {sf}: {ex}")
            try:
                sf.unlink(missing_ok=True)
            except Exception:
                pass
    return killed


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
            try:
                child_pid_int = int(child_pid)
                parent_pid_int = int(parent_pid)
            except Exception:
                sf.unlink(missing_ok=True)
                continue

            # Skip our own session files — they belong to the current process
            if parent_pid_int == my_pid:
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

            parent_alive = _session_parent_alive(data, parent_pid_int)
            child_alive = _pid_alive(child_pid_int)

            if not parent_alive:
                # Parent is dead — this is a stale session file
                if child_alive and _session_allows_child_cleanup(data, child_pid_int):
                    if not _cleanup_attempt_due(data, now):
                        continue
                    logger.warning(
                        f"[ProcessGuard] L4: Killing orphan PID {child_pid_int} "
                        f"(parent {parent_pid_int} dead)"
                    )
                    terminated = terminate_process_tree(child_pid_int, include_root=True, wait=True)
                    if terminated or not _pid_alive(child_pid_int):
                        killed += 1
                        sf.unlink(missing_ok=True)
                    else:
                        _mark_cleanup_pending(sf, data, "kill attempted but process remained alive")
                else:
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
# L5 - Parent watchdog
# ---------------------------------------------------------------------------

def _wait_for_parent_exit(parent_pid: int, parent_create_time: Optional[int] = None) -> None:
    """Block until parent_pid exits without holding a long-lived process handle.

    Windows keeps a process object alive while another process holds an open
    handle to it.  The watchdog used to wait on a single ``OpenProcess`` handle
    for the entire parent lifetime; if the watchdog wedged, that reference could
    delay object/file-handle cleanup.  Polling keeps every probe short-lived and
    preserves the PID-reuse signature check.
    """
    if parent_pid <= 0 or parent_pid == os.getpid():
        return

    while True:
        alive, actual_create_time = _watchdog_process_state(parent_pid)
        if not alive:
            return
        if parent_create_time is not None and actual_create_time is not None and actual_create_time != parent_create_time:
            return
        time.sleep(_PARENT_WATCHDOG_POLL_SECONDS)


def _run_parent_watchdog(parent_pid: int, session_dir: Path, parent_create_time: Optional[int] = None) -> int:
    """Watch a parent process and clean registered children after parent death."""
    _wait_for_parent_exit(parent_pid, parent_create_time)
    time.sleep(0.25)
    killed = _cleanup_session_files_for_parent(session_dir, parent_pid)
    if killed:
        logger.info(f"[ProcessGuard] L5: Cleaned up {killed} child process(es)")
    return 0


def _watchdog_creationflags(*, allow_breakaway: bool) -> int:
    flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    if allow_breakaway:
        flags |= _CREATE_BREAKAWAY_FROM_JOB
    return flags


def _watchdog_executable() -> str:
    """Use pythonw.exe for the watchdog on Windows so no console is shown."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def _start_parent_watchdog(session_dir: Path) -> None:
    """Start a detached helper that survives parent crashes where possible."""
    global _watchdog_process
    if sys.platform != "win32":
        return
    if os.environ.get("AGENTCLI_PROCESS_GUARD_WATCHDOG") == "1":
        return
    if _watchdog_process is not None and _watchdog_process.poll() is None:
        return

    cmd = [
        _watchdog_executable(),
        str(Path(__file__).resolve()),
        "--watch-parent",
        str(os.getpid()),
        str(session_dir),
        str(_pid_create_time_ticks(os.getpid()) or ""),
    ]
    env = dict(os.environ)
    env["AGENTCLI_PROCESS_GUARD_WATCHDOG"] = "1"

    for allow_breakaway in (True, False):
        try:
            _watchdog_process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=_watchdog_creationflags(allow_breakaway=allow_breakaway),
                env=env,
            )
            logger.debug(
                "[ProcessGuard] L5: Parent watchdog started "
                f"(pid={_watchdog_process.pid}, breakaway={allow_breakaway})"
            )
            if not allow_breakaway:
                logger.warning(
                    "[ProcessGuard] L5: Parent watchdog started without breakaway; "
                    "it may exit with the parent Job Object."
                )
            return
        except OSError as ex:
            logger.debug(f"[ProcessGuard] L5: Watchdog start failed (breakaway={allow_breakaway}): {ex}")
            continue
        except Exception as ex:
            logger.debug(f"[ProcessGuard] L5: Watchdog start failed: {ex}")
            return


# ---------------------------------------------------------------------------
# Windows diagnostics artifact sync
# ---------------------------------------------------------------------------

def _int_or_none(value: object) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _dict_or_empty(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_or_empty(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _windows_diagnostics_warning(
    kind: str,
    *,
    sample: Optional[int],
    ts: str,
    value: Optional[int],
    threshold: Optional[int],
) -> dict[str, Any]:
    message = kind
    if kind == "process_count_threshold":
        message = f"process_count {value} >= {threshold}"
    elif kind == "handle_count_threshold":
        message = f"handle_count {value} >= {threshold}"
    elif kind == "handle_growth":
        message = f"handle_growth {value} >= {threshold}"
    return {
        "kind": kind,
        "severity": "warning",
        "sample": sample,
        "ts": ts,
        "value": value,
        "threshold": threshold,
        "message": message,
    }


def normalize_windows_handle_diagnostics_snapshot(
    raw: dict[str, Any],
    *,
    previous_snapshot: Optional[dict[str, Any]] = None,
    process_count_threshold: int = DEFAULT_WINDOWS_PROCESS_COUNT_WARNING_THRESHOLD,
    handle_count_threshold: int = DEFAULT_WINDOWS_HANDLE_COUNT_WARNING_THRESHOLD,
    handle_growth_threshold: int = DEFAULT_WINDOWS_HANDLE_GROWTH_WARNING_THRESHOLD,
) -> dict[str, Any]:
    """Normalize one collector snapshot into a stable Python-side shape."""
    sample = _int_or_none(raw.get("sample"))
    ts = str(raw.get("ts") or raw.get("timestamp") or "").strip()
    process_tree = _list_or_empty(raw.get("processTree") or raw.get("process_tree"))
    process_count = _int_or_none(raw.get("processCount"))
    if process_count is None:
        process_count = len(process_tree)

    resource_pressure = _dict_or_empty(raw.get("resourcePressure") or raw.get("resource_pressure"))
    system_counters = _dict_or_empty(raw.get("systemCounters") or raw.get("system_counters"))
    top_handle_processes = _list_or_empty(raw.get("topHandleProcesses") or raw.get("top_handle_processes"))
    top_process = _dict_or_empty(resource_pressure.get("topProcess"))
    if not top_process and top_handle_processes:
        top_process = _dict_or_empty(top_handle_processes[0])

    system_handle_count = _int_or_none(resource_pressure.get("systemHandleCount"))
    if system_handle_count is None:
        system_handle_count = _int_or_none(system_counters.get("handle_count"))
    system_handle_limit = _int_or_none(resource_pressure.get("systemHandleLimit"))
    if system_handle_limit is None or system_handle_limit <= 0:
        system_handle_limit = int(handle_count_threshold)

    top_process_handle_count = _int_or_none(top_process.get("handleCount"))
    previous_handle_count = _int_or_none(
        (previous_snapshot or {}).get("system_handle_count")
    )
    handle_growth = None
    if system_handle_count is not None and previous_handle_count is not None:
        handle_growth = system_handle_count - previous_handle_count

    warnings: list[dict[str, Any]] = []
    if process_count is not None and process_count_threshold > 0 and process_count >= process_count_threshold:
        warnings.append(
            _windows_diagnostics_warning(
                "process_count_threshold",
                sample=sample,
                ts=ts,
                value=process_count,
                threshold=int(process_count_threshold),
            )
        )
    if (
        system_handle_count is not None
        and system_handle_limit is not None
        and system_handle_limit > 0
        and system_handle_count >= system_handle_limit
    ):
        warnings.append(
            _windows_diagnostics_warning(
                "handle_count_threshold",
                sample=sample,
                ts=ts,
                value=system_handle_count,
                threshold=system_handle_limit,
            )
        )
    if (
        handle_growth is not None
        and handle_growth_threshold > 0
        and handle_growth >= handle_growth_threshold
    ):
        warnings.append(
            _windows_diagnostics_warning(
                "handle_growth",
                sample=sample,
                ts=ts,
                value=handle_growth,
                threshold=int(handle_growth_threshold),
            )
        )

    return {
        "sample": sample,
        "ts": ts,
        "process_count": process_count,
        "process_count_threshold": int(process_count_threshold),
        "system_handle_count": system_handle_count,
        "system_handle_limit": system_handle_limit,
        "top_process_handle_count": top_process_handle_count,
        "top_process_name": str(top_process.get("name") or "").strip(),
        "top_process_pid": _int_or_none(top_process.get("pid")),
        "handle_growth": handle_growth,
        "handle_growth_threshold": int(handle_growth_threshold),
        "source_repo_root": str(raw.get("repoRoot") or raw.get("repo_root") or "").strip(),
        "source_run_dir": str(raw.get("runDir") or raw.get("run_dir") or "").strip(),
        "resource_pressure_exceeded": bool(resource_pressure.get("exceeded")),
        "warning_kinds": [str(item.get("kind") or "") for item in warnings],
        "warnings": warnings,
    }


def read_windows_handle_diagnostics_jsonl(
    path: Path,
    *,
    process_count_threshold: int = DEFAULT_WINDOWS_PROCESS_COUNT_WARNING_THRESHOLD,
    handle_count_threshold: int = DEFAULT_WINDOWS_HANDLE_COUNT_WARNING_THRESHOLD,
    handle_growth_threshold: int = DEFAULT_WINDOWS_HANDLE_GROWTH_WARNING_THRESHOLD,
) -> dict[str, Any]:
    """Read collector JSONL snapshots without requiring PowerShell execution."""
    source = Path(path)
    result: dict[str, Any] = {
        "status": "missing",
        "source_path": source.as_posix(),
        "source_exists": False,
        "snapshots": [],
        "warnings": [],
        "errors": [],
        "latest_snapshot": {},
        "summary": {
            "healthy": True,
            "snapshot_count": 0,
            "warning_count": 0,
            "warning_kinds": [],
            "process_count_threshold": int(process_count_threshold),
            "handle_count_threshold": int(handle_count_threshold),
            "handle_growth_threshold": int(handle_growth_threshold),
            "max_process_count": 0,
            "max_handle_count": 0,
            "max_handle_growth": 0,
        },
    }
    if not source.exists() or not source.is_file():
        return result

    result["source_exists"] = True
    snapshots: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    previous_snapshot: Optional[dict[str, Any]] = None

    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception as ex:
                    errors.append(
                        {
                            "line": line_number,
                            "message": str(ex),
                        }
                    )
                    continue
                if not isinstance(payload, dict):
                    errors.append(
                        {
                            "line": line_number,
                            "message": "snapshot must be a JSON object",
                        }
                    )
                    continue
                normalized = normalize_windows_handle_diagnostics_snapshot(
                    payload,
                    previous_snapshot=previous_snapshot,
                    process_count_threshold=process_count_threshold,
                    handle_count_threshold=handle_count_threshold,
                    handle_growth_threshold=handle_growth_threshold,
                )
                normalized["line"] = line_number
                snapshots.append(normalized)
                warnings.extend(normalized.get("warnings") or [])
                previous_snapshot = normalized
    except Exception as ex:
        errors.append({"line": 0, "message": str(ex)})

    if not snapshots and errors:
        status = "malformed"
    elif snapshots and errors:
        status = "partial"
    elif snapshots:
        status = "ok"
    else:
        status = "empty"

    latest_snapshot = dict(snapshots[-1]) if snapshots else {}
    max_process_count = 0
    max_handle_count = 0
    max_handle_growth = 0
    for snapshot in snapshots:
        process_count = _int_or_none(snapshot.get("process_count"))
        handle_count = _int_or_none(snapshot.get("system_handle_count"))
        handle_growth = _int_or_none(snapshot.get("handle_growth"))
        if process_count is not None:
            max_process_count = max(max_process_count, process_count)
        if handle_count is not None:
            max_handle_count = max(max_handle_count, handle_count)
        if handle_growth is not None:
            max_handle_growth = max(max_handle_growth, handle_growth)

    warning_kinds: list[str] = []
    seen_warning_kinds: set[str] = set()
    for item in warnings:
        kind = str(item.get("kind") or "").strip()
        if not kind or kind in seen_warning_kinds:
            continue
        seen_warning_kinds.add(kind)
        warning_kinds.append(kind)
    result.update(
        {
            "status": status,
            "snapshots": snapshots,
            "warnings": warnings,
            "errors": errors,
            "latest_snapshot": latest_snapshot,
            "summary": {
                "healthy": status == "ok" and not warnings,
                "snapshot_count": len(snapshots),
                "warning_count": len(warnings),
                "warning_kinds": warning_kinds,
                "process_count_threshold": int(process_count_threshold),
                "handle_count_threshold": int(
                    latest_snapshot.get("system_handle_limit") or handle_count_threshold
                ),
                "handle_growth_threshold": int(handle_growth_threshold),
                "latest_process_count": _int_or_none(latest_snapshot.get("process_count")),
                "latest_handle_count": _int_or_none(latest_snapshot.get("system_handle_count")),
                "latest_handle_growth": _int_or_none(latest_snapshot.get("handle_growth")),
                "max_process_count": max_process_count,
                "max_handle_count": max_handle_count,
                "max_handle_growth": max_handle_growth,
            },
        }
    )
    return result


def _windows_handle_diagnostics_candidates(repo: Path, run_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    run_dir_path = Path(run_dir)
    run_artifact_root = run_dir_path.parents[1] if len(run_dir_path.parents) >= 2 else None
    roots = [
        run_dir_path / "diagnostics",
        (run_artifact_root / "diagnostics") if run_artifact_root is not None else None,
        Path(repo) / ".AgentCLI" / "diagnostics",
    ]
    seen_roots: set[str] = set()
    for root in roots:
        if root is None:
            continue
        root_key = root.as_posix()
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        if not root.exists() or not root.is_dir():
            continue
        for path in root.glob(_WINDOWS_HANDLE_DIAGNOSTICS_GLOB):
            if ".normalized." in path.name.lower():
                continue
            if path.is_file():
                candidates.append(path)
    candidates.sort(
        key=lambda item: (
            0 if item.parent == (run_dir_path / "diagnostics") else 1,
            -item.stat().st_mtime,
            item.name,
        )
    )
    return candidates


def find_windows_handle_diagnostics_source(repo: Path, run_dir: Path) -> Optional[Path]:
    candidates = _windows_handle_diagnostics_candidates(repo, run_dir)
    return candidates[0] if candidates else None


def write_windows_handle_diagnostics_artifacts(
    repo: Path,
    run_dir: Path,
    *,
    process_count_threshold: int = DEFAULT_WINDOWS_PROCESS_COUNT_WARNING_THRESHOLD,
    handle_count_threshold: int = DEFAULT_WINDOWS_HANDLE_COUNT_WARNING_THRESHOLD,
    handle_growth_threshold: int = DEFAULT_WINDOWS_HANDLE_GROWTH_WARNING_THRESHOLD,
) -> dict[str, Any]:
    """Mirror collector snapshots into run artifacts as normalized JSON/JSONL."""
    if sys.platform != "win32":
        return {
            "status": "noop",
            "reason": "platform_not_windows",
            "artifacts": {},
        }

    source_path = find_windows_handle_diagnostics_source(repo, run_dir)
    if source_path is None:
        return {
            "status": "missing",
            "reason": "diagnostics_not_found",
            "artifacts": {},
        }

    normalized = read_windows_handle_diagnostics_jsonl(
        source_path,
        process_count_threshold=process_count_threshold,
        handle_count_threshold=handle_count_threshold,
        handle_growth_threshold=handle_growth_threshold,
    )
    if normalized.get("status") in {"missing", "empty"}:
        return normalized

    artifact_dir = Path(run_dir) / "diagnostics"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifact_dir / _WINDOWS_HANDLE_DIAGNOSTICS_ARTIFACT
    normalized_jsonl_path = artifact_dir / _WINDOWS_HANDLE_DIAGNOSTICS_NORMALIZED_JSONL

    payload = dict(normalized)
    payload["artifact_path"] = summary_path.as_posix()
    payload["normalized_jsonl_path"] = normalized_jsonl_path.as_posix()
    payload["artifacts"] = {
        "summary_json": summary_path.as_posix(),
        "normalized_jsonl": normalized_jsonl_path.as_posix(),
        "source_jsonl": source_path.as_posix(),
    }

    try:
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            errors="replace",
        )
    except Exception as ex:
        logger.debug(f"[ProcessGuard] Failed to write diagnostics summary {summary_path}: {ex}")
    try:
        lines = [
            json.dumps(snapshot, ensure_ascii=False, default=str)
            for snapshot in payload.get("snapshots") or []
            if isinstance(snapshot, dict)
        ]
        normalized_jsonl_path.write_text(
            ("\n".join(lines) + ("\n" if lines else "")),
            encoding="utf-8",
            errors="replace",
        )
    except Exception as ex:
        logger.debug(
            f"[ProcessGuard] Failed to write diagnostics jsonl {normalized_jsonl_path}: {ex}"
        )
    return payload


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

        # L5: detached watchdog for parent crash/SIGKILL paths.
        try:
            _start_parent_watchdog(_session_dir)
        except Exception as ex:
            logger.debug(f"[ProcessGuard] L5: Watchdog setup failed: {ex}")

        _initialized = True

    logger.info(
        "[ProcessGuard] Initialized (L1=%s, L2=atexit, L3=signals, L4=orphan-scan, L5=watchdog)",
        "JobObject" if _job_handle else "skipped",
    )


def _main(argv: list[str]) -> int:
    if len(argv) in (4, 5) and argv[1] == "--watch-parent":
        try:
            parent_pid = int(argv[2])
            session_dir = Path(argv[3]).expanduser().resolve()
            parent_create_time = int(argv[4]) if len(argv) == 5 and str(argv[4]).strip() else None
        except Exception:
            return 2
        return _run_parent_watchdog(parent_pid, session_dir, parent_create_time)
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess on Windows
    raise SystemExit(_main(sys.argv))
