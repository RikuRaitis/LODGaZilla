"""Own Windows batch descendants, including pools and external converters.

Job membership is inherited by children. The non-inheritable owner handle
also provides kill-on-close protection if the GUI process exits unexpectedly.
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
"""

import ctypes
import os
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("Times", ctypes.c_longlong * 4), ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD), ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]


class WindowsProcessTree:
    def __init__(self):
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self._api, name)
            function.argtypes, function.restype = args, result
        self._handle = self._api.CreateJobObjectW(None, None)
        self._check(self._handle)
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self._check(self._api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self._api.CloseHandle(self._handle)
            self._handle = None
            raise

    @staticmethod
    def _check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid: int) -> None:
        handle = self._api.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        self._check(handle)
        try:
            self._check(self._api.AssignProcessToJobObject(self._handle, handle))
        finally:
            self._api.CloseHandle(handle)

    def active_processes(self) -> int:
        if self._handle is None:
            return 0
        info = _Accounting()
        self._check(self._api.QueryInformationJobObject(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
        return int(info.ActiveProcesses)

    def close(self, timeout: float = 5.0) -> None:
        """Terminate any survivors and verify that all descendants have exited."""
        if self._handle is None:
            return
        try:
            if self.active_processes():
                self._check(self._api.TerminateJobObject(self._handle, 1))
                deadline = time.monotonic() + timeout
                while self.active_processes():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Batch process tree did not stop before the shutdown deadline")
                    time.sleep(0.01)
        finally:
            # Kill-on-close still applies if querying/terminating the job failed.
            self._api.CloseHandle(self._handle)
            self._handle = None


def start_process_tree(process, start_event):
    """Start a coordinator; release it only after Windows owns its descendants.

    Other platforms retain the cooperative cancellation/pool shutdown path.
    Coordinators must wait for start_event before launching any work.
    """
    tree = WindowsProcessTree() if os.name == "nt" else None
    try:
        process.start()
        if tree is not None:
            tree.assign(process.pid)
        start_event.set()
        return tree
    except BaseException:
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(3)
        if tree is not None:
            tree.close()
        raise


def popen_in_tree(args, **kwargs):
    """Start a subprocess that cannot start children outside its process tree.

    On Windows the process starts suspended, joins a job object and only then runs, so
    everything it starts belongs to the job; closing the tree ends them all. Elsewhere it
    starts in its own session, whose process group ``kill_process_tree`` ends.
    Returns ``(process, tree)``; ``tree`` is None outside Windows.
    """
    if os.name != "nt":
        import subprocess
        return subprocess.Popen(args, start_new_session=True, **kwargs), None
    import subprocess
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    tree = WindowsProcessTree()
    try:
        process = subprocess.Popen(args, **kwargs)
    except BaseException:
        tree.close()
        raise
    try:
        tree.assign(process.pid)
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes, ntdll.NtResumeProcess.restype = [wintypes.HANDLE], ctypes.c_long
        status = ntdll.NtResumeProcess(int(process._handle))
        if status < 0:
            raise OSError(f"Could not resume the process (NTSTATUS {status & 0xFFFFFFFF:#010x})")
    except BaseException:
        process.kill()
        tree.close()
        raise
    return process, tree


def kill_process_tree(process, tree) -> None:
    """End a process started with ``popen_in_tree`` and everything it started."""
    if tree is not None:
        try:
            tree.close()
        except (OSError, TimeoutError):
            pass
    elif os.name != "nt" and process.poll() is None:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    if process.poll() is None:
        process.kill()
