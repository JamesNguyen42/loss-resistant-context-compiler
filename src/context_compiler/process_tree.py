"""Portable process-tree ownership and termination primitives."""

from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from typing import Any

WINDOWS_CREATE_SUSPENDED = 0x00000004
_DARWIN_MAX_PROCESS_GROUP_MEMBERS = 4096
_DARWIN_PROCESS_GROUP_PID_CAPACITY = _DARWIN_MAX_PROCESS_GROUP_MEMBERS + 1
_DARWIN_PROC_PIDTBSDINFO = 3
_DARWIN_PROC_PIDTBSDINFO_INCLUDE_ZOMBIES = 1
_DARWIN_PROC_BSD_INFO_SIZE = 136
_DARWIN_PROCESS_STATUS_ZOMBIE = 5
_PROCESS_GROUP_GRACE_SECONDS = 0.5
_POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)
_WINDOWS_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_WINDOWS_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_TH32CS_SNAPTHREAD = 0x00000004
_WINDOWS_THREAD_SUSPEND_RESUME = 0x0002


class ProcessTreeError(RuntimeError):
    """A process tree could not be placed inside the requested boundary."""


class _DarwinProcBsdInfo(ctypes.Structure):
    """Exact public Darwin ``struct proc_bsdinfo`` layout."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _load_darwin_libproc(
    error_type: type[RuntimeError],
) -> Any:
    """Load the two public libproc entry points used by zombie proof."""

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        list_group_pids = libproc.proc_listpgrppids
        process_info = libproc.proc_pidinfo
    except (AttributeError, OSError) as exc:
        raise error_type("could not load Darwin process inspection APIs") from exc
    list_group_pids.argtypes = (
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    list_group_pids.restype = ctypes.c_int
    process_info.argtypes = (
        ctypes.c_int32,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    process_info.restype = ctypes.c_int
    return libproc


def _darwin_process_group_pids(
    libproc: Any,
    process_group_id: int,
    *,
    error_type: type[RuntimeError],
) -> tuple[int, ...]:
    """Return one bounded group-member enumeration or fail on truncation."""

    pid_buffer_type = ctypes.c_int32 * _DARWIN_PROCESS_GROUP_PID_CAPACITY
    pid_buffer = pid_buffer_type()
    ctypes.set_errno(0)
    count = int(
        libproc.proc_listpgrppids(
            process_group_id,
            pid_buffer,
            ctypes.sizeof(pid_buffer),
        )
    )
    if count < 0:
        error = ctypes.get_errno()
        raise error_type(
            "could not enumerate the anchored Darwin process group "
            f"(error {error})"
        )
    if count == 0:
        error = ctypes.get_errno()
        if error != 0:
            raise error_type(
                "could not enumerate the anchored Darwin process group "
                f"(error {error})"
            )
        # A successful empty enumeration is not independently sufficient to
        # prove that the anchored group vanished. Inspect the expected
        # unreaped leader with PROC_PIDTBSDINFO's include-zombies flag below.
        return ()
    if count >= _DARWIN_PROCESS_GROUP_PID_CAPACITY:
        raise error_type(
            "anchored Darwin process-group enumeration was truncated"
        )
    members = tuple(sorted(int(pid_buffer[index]) for index in range(count)))
    if any(process_id <= 0 for process_id in members):
        raise error_type(
            "anchored Darwin process-group enumeration returned an invalid PID"
        )
    if len(set(members)) != len(members):
        raise error_type(
            "anchored Darwin process-group enumeration returned duplicate PIDs"
        )
    return members


def _darwin_process_identity(
    libproc: Any,
    process_id: int,
    process_group_id: int,
    *,
    error_type: type[RuntimeError],
) -> tuple[int, int, int]:
    """Read and validate one exact BSD process record."""

    actual_struct_size = ctypes.sizeof(_DarwinProcBsdInfo)
    if actual_struct_size != _DARWIN_PROC_BSD_INFO_SIZE:
        raise error_type(
            "local Darwin BSD process-info layout has an unexpected size"
        )
    process_info = _DarwinProcBsdInfo()
    ctypes.set_errno(0)
    returned_size = int(
        libproc.proc_pidinfo(
            process_id,
            _DARWIN_PROC_PIDTBSDINFO,
            _DARWIN_PROC_PIDTBSDINFO_INCLUDE_ZOMBIES,
            ctypes.byref(process_info),
            actual_struct_size,
        )
    )
    if returned_size != actual_struct_size:
        error = ctypes.get_errno()
        raise error_type(
            "could not read an exact Darwin BSD process-info record for "
            f"PID {process_id} (returned {returned_size} bytes, error {error})"
        )
    if int(process_info.pbi_pid) != process_id:
        raise error_type(
            f"Darwin process-info PID changed while inspecting PID {process_id}"
        )
    if int(process_info.pbi_pgid) != process_group_id:
        raise error_type(
            f"PID {process_id} left the anchored Darwin process group"
        )
    if int(process_info.pbi_status) != _DARWIN_PROCESS_STATUS_ZOMBIE:
        raise error_type(
            f"anchored Darwin process group still contains live PID {process_id}"
        )
    return (
        process_id,
        int(process_info.pbi_start_tvsec),
        int(process_info.pbi_start_tvusec),
    )


def _darwin_process_group_snapshot(
    libproc: Any,
    process_group_id: int,
    expected_leader_pid: int,
    *,
    error_type: type[RuntimeError],
) -> tuple[tuple[int, int, int], ...]:
    """Capture one bounded, fully inspected all-zombie group snapshot.

    Inspect the known WNOWAIT leader directly with include-zombies semantics,
    even if the group enumeration is empty, while also inspecting every PID
    that the bounded enumeration returns. The caller requires two identical
    snapshots.
    """

    members = _darwin_process_group_pids(
        libproc,
        process_group_id,
        error_type=error_type,
    )
    process_ids = tuple(sorted({expected_leader_pid, *members}))
    return tuple(
        _darwin_process_identity(
            libproc,
            process_id,
            process_group_id,
            error_type=error_type,
        )
        for process_id in process_ids
    )


def prove_darwin_process_group_all_zombies(
    process_group_id: int,
    *,
    expected_leader_pid: int,
    error_type: type[RuntimeError] = ProcessTreeError,
) -> None:
    """Prove that one anchored Darwin group has only stable zombie members.

    This exceptional-path proof is intentionally conservative. Any API error,
    inaccessible or live member, truncated enumeration, PID reuse, membership
    race, or unreadable WNOWAIT leader rejects the proof.
    """

    for label, value in (
        ("process-group ID", process_group_id),
        ("expected leader PID", expected_leader_pid),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > 0x7FFFFFFF
        ):
            raise error_type(f"Darwin {label} must be a positive signed PID")
    if sys.platform != "darwin":
        raise error_type("Darwin all-zombie proof is available only on Darwin")
    if process_group_id != expected_leader_pid:
        raise error_type(
            "Darwin all-zombie proof requires the WNOWAIT group leader"
        )

    libproc = _load_darwin_libproc(error_type)
    before = _darwin_process_group_snapshot(
        libproc,
        process_group_id,
        expected_leader_pid,
        error_type=error_type,
    )
    after = _darwin_process_group_snapshot(
        libproc,
        process_group_id,
        expected_leader_pid,
        error_type=error_type,
    )
    before_members = tuple(record[0] for record in before)
    after_members = tuple(record[0] for record in after)
    if before_members != after_members:
        raise error_type(
            "anchored Darwin process-group membership changed during inspection"
        )
    if before != after:
        raise error_type(
            "anchored Darwin process identity changed during inspection"
        )


class WindowsJob:
    """Own a Windows Job Object that bounds a subprocess tree."""

    def __init__(
        self,
        handle: object,
        kernel32: Any,
        error_type: type[RuntimeError],
    ) -> None:
        self._handle = handle
        self._kernel32 = kernel32
        self._error_type = error_type

    @classmethod
    def create(
        cls,
        max_memory_mb: int | None = None,
        *,
        error_type: type[RuntimeError] = ProcessTreeError,
    ) -> WindowsJob:
        if os.name != "nt":
            raise error_type("Windows Job Objects are available only on Windows")
        if max_memory_mb is not None and (
            isinstance(max_memory_mb, bool)
            or not isinstance(max_memory_mb, int)
            or max_memory_mb <= 0
        ):
            raise error_type("Windows Job Object memory limit must be positive")

        import ctypes
        from ctypes import wintypes

        class JobObjectBasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JobObjectExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JobObjectBasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.IsProcessInJob.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        )
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            error = ctypes.get_last_error()
            raise error_type(
                f"could not create Windows Job Object (error {error})"
            )
        job = cls(handle, kernel32, error_type)
        information = JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            _WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if max_memory_mb is not None:
            memory_bytes = max_memory_mb * 1024 * 1024
            information.BasicLimitInformation.LimitFlags |= (
                _WINDOWS_JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _WINDOWS_JOB_OBJECT_LIMIT_JOB_MEMORY
            )
            information.ProcessMemoryLimit = memory_bytes
            information.JobMemoryLimit = memory_bytes
        if not kernel32.SetInformationJobObject(
            handle,
            _WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            job.close()
            raise error_type(
                f"could not configure Windows Job Object (error {error})"
            )
        return job

    @property
    def handle(self) -> object:
        return self._handle

    def assign(self, process: subprocess.Popen[Any]) -> None:
        import ctypes

        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise self._error_type("subprocess has no Windows process handle")
        if not self._kernel32.AssignProcessToJobObject(
            self._handle,
            process_handle,
        ):
            error = ctypes.get_last_error()
            raise self._error_type(
                f"could not assign subprocess to Windows Job Object (error {error})"
            )

    def contains(self, process: subprocess.Popen[Any]) -> bool:
        import ctypes
        from ctypes import wintypes

        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            return False
        result = wintypes.BOOL()
        if not self._kernel32.IsProcessInJob(
            process_handle,
            self._handle,
            ctypes.byref(result),
        ):
            error = ctypes.get_last_error()
            raise self._error_type(
                f"could not verify Windows Job Object membership (error {error})"
            )
        return bool(result.value)

    def terminate(self) -> None:
        import ctypes

        if self._handle and not self._kernel32.TerminateJobObject(
            self._handle,
            1,
        ):
            error = ctypes.get_last_error()
            raise self._error_type(
                f"could not terminate Windows Job Object (error {error})"
            )
        deadline = time.monotonic() + 1.0
        while self._active_processes() != 0:
            if time.monotonic() >= deadline:
                raise self._error_type(
                    "Windows Job Object processes did not terminate"
                )
            time.sleep(0.01)

    def _active_processes(self) -> int:
        import ctypes
        from ctypes import wintypes

        class JobObjectBasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", wintypes.LARGE_INTEGER),
                ("TotalKernelTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        information = JobObjectBasicAccountingInformation()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            1,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            error = ctypes.get_last_error()
            raise self._error_type(
                f"could not query Windows Job Object (error {error})"
            )
        return int(information.ActiveProcesses)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def resume_windows_process(
    process: subprocess.Popen[Any],
    *,
    error_type: type[RuntimeError] = ProcessTreeError,
) -> None:
    """Resume the primary thread of a newly created suspended process."""

    if os.name != "nt":
        raise error_type("suspended-process resume is available only on Windows")

    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (
        wintypes.DWORD,
        wintypes.DWORD,
    )
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    )
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    )
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(
        _WINDOWS_TH32CS_SNAPTHREAD,
        0,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        error = ctypes.get_last_error()
        raise error_type(
            f"could not enumerate suspended subprocess threads (error {error})"
        )
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found_thread_id: int | None = None
        has_entry = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while has_entry:
            if entry.th32OwnerProcessID == process.pid:
                found_thread_id = int(entry.th32ThreadID)
                break
            has_entry = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        if found_thread_id is None:
            raise error_type(
                "could not find the suspended subprocess primary thread"
            )
        thread = kernel32.OpenThread(
            _WINDOWS_THREAD_SUSPEND_RESUME,
            False,
            found_thread_id,
        )
        if not thread:
            error = ctypes.get_last_error()
            raise error_type(
                f"could not open the suspended subprocess thread (error {error})"
            )
        try:
            previous_count = kernel32.ResumeThread(thread)
            if previous_count == 0xFFFFFFFF:
                error = ctypes.get_last_error()
                raise error_type(
                    f"could not resume the bounded subprocess (error {error})"
                )
            if previous_count == 0:
                raise error_type(
                    "subprocess primary thread was not suspended before "
                    "Job Object assignment"
                )
        finally:
            kernel32.CloseHandle(thread)
    finally:
        kernel32.CloseHandle(snapshot)


def posix_process_exited_without_reaping(
    process: subprocess.Popen[Any],
    *,
    error_type: type[RuntimeError] = ProcessTreeError,
) -> bool:
    """Observe one owned POSIX leader without releasing its PID/PGID anchor."""

    if process.returncode is not None:
        raise error_type(
            "owned POSIX process was reaped before process-group cleanup"
        )
    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except InterruptedError:
        return False
    except ChildProcessError as exc:
        raise error_type(
            "owned POSIX process was reaped before process-group cleanup"
        ) from exc
    except OSError as exc:
        raise error_type(
            "could not observe owned POSIX process exit without reaping"
        ) from exc
    return result is not None


def _verify_darwin_process_group_after_sigkill(
    process: subprocess.Popen[Any],
    *,
    error_type: type[RuntimeError],
) -> None:
    """Require disappearance or stable all-zombie proof after Darwin SIGKILL."""

    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise error_type(
                    "could not verify the owned POSIX process group after SIGKILL"
                ) from exc
            break
        except OSError as exc:
            raise error_type(
                "could not verify the owned POSIX process group after SIGKILL"
            ) from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    prove_darwin_process_group_all_zombies(
        process.pid,
        expected_leader_pid=process.pid,
        error_type=error_type,
    )


def _force_anchored_posix_process_group(
    process: subprocess.Popen[Any],
    *,
    error_type: type[RuntimeError],
) -> None:
    """Deliver the final group signal while the direct leader remains waitable."""

    try:
        os.killpg(process.pid, _POSIX_SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        if exc.errno != errno.EPERM or sys.platform != "darwin":
            raise error_type("could not kill the owned POSIX process group") from exc
    except OSError as exc:
        raise error_type("could not kill the owned POSIX process group") from exc
    if sys.platform == "darwin":
        _verify_darwin_process_group_after_sigkill(
            process,
            error_type=error_type,
        )


def terminate_anchored_posix_process_group(
    process: subprocess.Popen[Any],
    *,
    error_type: type[RuntimeError] = ProcessTreeError,
) -> None:
    """Terminate one process group while its unreaped leader anchors the PGID."""

    direct_process_exited = posix_process_exited_without_reaping(
        process,
        error_type=error_type,
    )
    if direct_process_exited:
        _force_anchored_posix_process_group(
            process,
            error_type=error_type,
        )
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        if exc.errno == errno.EPERM and sys.platform == "darwin":
            # The leader can exit after the initial WNOWAIT observation but
            # before this signal. XNU skips zombies during group signaling and
            # reports EPERM for a zombie-only group, so accept that race only
            # after a fresh anchored observation and the full bounded proof.
            if posix_process_exited_without_reaping(
                process,
                error_type=error_type,
            ):
                prove_darwin_process_group_all_zombies(
                    process.pid,
                    expected_leader_pid=process.pid,
                    error_type=error_type,
                )
                return
        raise error_type(
            "could not terminate the owned POSIX process group"
        ) from exc
    except OSError as exc:
        raise error_type(
            "could not terminate the owned POSIX process group"
        ) from exc

    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while True:
        direct_process_exited = posix_process_exited_without_reaping(
            process,
            error_type=error_type,
        )
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            if (
                exc.errno == errno.EPERM
                and sys.platform == "darwin"
                and direct_process_exited
            ):
                prove_darwin_process_group_all_zombies(
                    process.pid,
                    expected_leader_pid=process.pid,
                    error_type=error_type,
                )
                return
            raise error_type(
                "could not verify the owned POSIX process group after SIGTERM"
            ) from exc
        except OSError as exc:
            raise error_type(
                "could not verify the owned POSIX process group after SIGTERM"
            ) from exc
        if direct_process_exited:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    _force_anchored_posix_process_group(
        process,
        error_type=error_type,
    )


def terminate_posix_process_group(process_group_id: int) -> None:
    """Signal a caller-owned group while the caller preserves PGID ownership.

    New subprocess code should use ``terminate_anchored_posix_process_group``.
    This compatibility helper cannot prove that a bare numeric PGID was not
    reused after its original leader was reaped.
    """

    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    with suppress(ProcessLookupError):
        os.killpg(process_group_id, _POSIX_SIGKILL)


def terminate_process_tree(
    process: subprocess.Popen[Any],
    windows_job: WindowsJob | None = None,
) -> None:
    """Signal or terminate a subprocess through its owned platform boundary."""

    if os.name == "nt":
        if process.poll() is not None:
            return
        if windows_job is not None:
            with suppress(RuntimeError):
                windows_job.terminate()
            if process.poll() is None:
                process.kill()
            return
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
        )
        if completed.returncode != 0 and process.poll() is None:
            process.kill()
    else:
        terminate_anchored_posix_process_group(process)
