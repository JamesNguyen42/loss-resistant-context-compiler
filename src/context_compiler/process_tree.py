"""Portable process-tree ownership and termination primitives."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from typing import Any

WINDOWS_CREATE_SUSPENDED = 0x00000004
_WINDOWS_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_WINDOWS_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_TH32CS_SNAPTHREAD = 0x00000004
_WINDOWS_THREAD_SUSPEND_RESUME = 0x0002


class ProcessTreeError(RuntimeError):
    """A process tree could not be placed inside the requested boundary."""


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


def terminate_posix_process_group(process_group_id: int) -> None:
    """Terminate a process group, escalating after a short grace period."""

    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    with suppress(ProcessLookupError):
        os.killpg(process_group_id, signal.SIGKILL)


def terminate_process_tree(
    process: subprocess.Popen[Any],
    windows_job: WindowsJob | None = None,
) -> None:
    """Terminate a live subprocess and every descendant in its owned tree."""

    if process.poll() is not None:
        return
    if os.name == "nt":
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
        terminate_posix_process_group(process.pid)
