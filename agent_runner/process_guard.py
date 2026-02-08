"""Process Guard: 4-layer defense against orphan child processes.

L1 - Windows Job Object (KILL_ON_JOB_CLOSE): OS-level automatic cleanup on parent exit.
L2 - PID tracking + atexit: Graceful cleanup on normal exit or unhandled exceptions.
L3 - Enhanced signal handlers: Kill children on SIGINT/SIGTERM/SIGBREAK.
L4 - Startup orphan cleanup: Detect and kill orphans from previous runs.
"""
from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_initialized = False
_tracked_pids: set[int] = set()
_session_dir: Optional[Path] = None
_stop_path_func: Optional[Callable[[], Optional[Path]]] = None
_job_handle: Optional[int] = None

# ---------------------------------------------------------------------------
# L1 - Windows Job Object
# ---------------------------------------------------------------------------

# Win32 constants
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


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


def _setup_job_object() -> Optional[int]:
    """Create a Windows Job Object with KILL_ON_JOB_CLOSE and assign current process."""
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        HANDLE = ctypes.c_void_p

        # Set proper return/arg types for Win32 API calls
        kernel32.CreateJobObjectW.restype = HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
        kernel32.GetCurrentProcess.restype = HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.GetLastError.restype = ctypes.c_uint32
        kernel32.GetLastError.argtypes = []

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
            # ERROR_ACCESS_DENIED (5) means process is already in a job object
            if err == 5:
                logger.debug("[ProcessGuard] Process already in a Job Object, L1 skipped")
            else:
                logger.warning(f"[ProcessGuard] Failed to assign to Job Object (err={err})")
            kernel32.CloseHandle(job)
            return None

        logger.info("[ProcessGuard] L1: Job Object created (KILL_ON_JOB_CLOSE)")
        return job
    except Exception as ex:
        logger.warning(f"[ProcessGuard] L1 setup failed: {ex}")
        return None


# ---------------------------------------------------------------------------
# L2 - PID tracking + atexit
# ---------------------------------------------------------------------------

def _get_session_dir() -> Path:
    """Return the session directory, creating it if needed."""
    if _session_dir is not None:
        return _session_dir
    fallback = Path.home() / ".agentcli" / "sessions"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _session_file(pid: int) -> Path:
    return _get_session_dir() / f"session_{pid}.json"


def register_pid(pid: int) -> None:
    """Register a child process PID for tracking."""
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
    except Exception:
        pass
    logger.debug(f"[ProcessGuard] Registered child PID {pid}")


def unregister_pid(pid: int) -> None:
    """Unregister a child process PID."""
    _tracked_pids.discard(pid)
    try:
        sf = _session_file(pid)
        if sf.exists():
            sf.unlink()
    except Exception:
        pass
    logger.debug(f"[ProcessGuard] Unregistered child PID {pid}")


def _kill_pid(pid: int, *, force: bool = False) -> None:
    """Kill a single process by PID."""
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            HANDLE = ctypes.c_void_p
            kernel32.OpenProcess.restype = HANDLE
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.TerminateProcess.restype = ctypes.c_int
            kernel32.TerminateProcess.argtypes = [HANDLE, ctypes.c_uint]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [HANDLE]
            handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


# WaitForSingleObject return values
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102


def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            HANDLE = ctypes.c_void_p
            _SYNCHRONIZE = 0x00100000
            kernel32.OpenProcess.restype = HANDLE
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.WaitForSingleObject.argtypes = [HANDLE, ctypes.c_uint32]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [HANDLE]
            handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_TERMINATE, False, pid)
            if not handle:
                return False
            # Wait with 0 timeout: returns WAIT_TIMEOUT if still running
            result = kernel32.WaitForSingleObject(handle, 0)
            kernel32.CloseHandle(handle)
            return result == _WAIT_TIMEOUT
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def terminate_all_children() -> None:
    """Terminate all tracked child processes (L2/L3 handler)."""
    if not _tracked_pids:
        return
    pids = list(_tracked_pids)
    logger.info(f"[ProcessGuard] Terminating {len(pids)} tracked child process(es): {pids}")

    # First pass: graceful terminate
    for pid in pids:
        _kill_pid(pid, force=False)

    # Brief wait for graceful exit
    time.sleep(0.5)

    # Second pass: force kill any survivors
    for pid in pids:
        if _pid_alive(pid):
            logger.info(f"[ProcessGuard] Force-killing PID {pid}")
            _kill_pid(pid, force=True)

    # Cleanup session files
    for pid in pids:
        _tracked_pids.discard(pid)
        try:
            sf = _session_file(pid)
            if sf.exists():
                sf.unlink()
        except Exception:
            pass


def _atexit_handler() -> None:
    """atexit handler: kill all tracked children on interpreter shutdown."""
    if _tracked_pids:
        logger.info("[ProcessGuard] L2: atexit cleanup triggered")
        terminate_all_children()


# ---------------------------------------------------------------------------
# L3 - Enhanced signal handlers
# ---------------------------------------------------------------------------

def _make_signal_handler(
    stop_path_func: Optional[Callable[[], Optional[Path]]],
) -> Callable[[int, object], None]:
    """Create a signal handler that writes STOP file + kills children."""

    def _handler(signum: int, frame: object) -> None:
        # Write STOP file for graceful runner loop exit
        if stop_path_func is not None:
            try:
                stop_path = stop_path_func()
                if stop_path is not None:
                    stop_path.parent.mkdir(parents=True, exist_ok=True)
                    stop_path.write_text(f"signal {signum}\n", encoding="utf-8")
            except Exception:
                pass

        # Kill tracked children immediately
        terminate_all_children()

        msg = f"[ProcessGuard] L3: Signal {signum} received, children terminated.\n"
        try:
            sys.stderr.write(msg)
            sys.stderr.flush()
        except Exception:
            pass

    return _handler


def install_signal_handlers(
    stop_path_func: Optional[Callable[[], Optional[Path]]] = None,
) -> None:
    """Install enhanced signal handlers (L3)."""
    func = stop_path_func or _stop_path_func
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

def _is_claude_process(pid: int) -> bool:
    """Heuristic: check if PID is a node/claude process."""
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
            return any(name in output for name in ("node", "claude"))
        else:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text(errors="replace").lower()
                return any(name in cmdline for name in ("node", "claude"))
    except Exception:
        pass
    return False


def cleanup_orphans(session_dir: Optional[Path] = None) -> int:
    """Scan session files and kill orphaned child processes (L4).

    Returns the number of orphans killed.
    """
    sd = session_dir or _get_session_dir()
    if not sd.exists():
        return 0

    killed = 0
    for sf in list(sd.glob("session_*.json")):
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            child_pid = data.get("child_pid")
            parent_pid = data.get("parent_pid")
            if child_pid is None or parent_pid is None:
                sf.unlink(missing_ok=True)
                continue

            parent_alive = _pid_alive(parent_pid)
            child_alive = _pid_alive(child_pid)

            if not parent_alive and child_alive:
                if _is_claude_process(child_pid):
                    logger.warning(
                        f"[ProcessGuard] L4: Killing orphan PID {child_pid} "
                        f"(parent {parent_pid} dead)"
                    )
                    _kill_pid(child_pid, force=True)
                    killed += 1
                else:
                    logger.debug(
                        f"[ProcessGuard] L4: PID {child_pid} alive but not claude/node, skipping"
                    )
            # Clean up stale session file regardless
            sf.unlink(missing_ok=True)
        except Exception as ex:
            logger.debug(f"[ProcessGuard] L4: Error processing {sf}: {ex}")
            try:
                sf.unlink(missing_ok=True)
            except Exception:
                pass

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
    """Initialize all process guard layers (idempotent).

    Args:
        session_dir: Directory for session PID files. Defaults to ~/.agentcli/sessions.
        stop_path_func: Callable returning the current STOP file path for signal handlers.
    """
    global _initialized, _session_dir, _stop_path_func, _job_handle

    if _initialized:
        # Update stop_path_func if provided (runner may set it later)
        if stop_path_func is not None:
            _stop_path_func = stop_path_func
        return

    if session_dir is not None:
        _session_dir = session_dir
        _session_dir.mkdir(parents=True, exist_ok=True)
    else:
        _session_dir = Path.home() / ".agentcli" / "sessions"
        _session_dir.mkdir(parents=True, exist_ok=True)

    _stop_path_func = stop_path_func

    # L1: Job Object
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
    logger.info("[ProcessGuard] Initialized (L1=%s, L2=atexit, L3=signals, L4=orphan-scan)",
                "JobObject" if _job_handle else "skipped")
