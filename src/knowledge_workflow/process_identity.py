"""Lightweight Windows process birth time without loading monitoring libraries."""
import ctypes
import os
from ctypes import wintypes


def creation_time(pid=None):
    if os.name != "nt":
        import psutil
        return psutil.Process(pid or os.getpid()).create_time()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    handle = kernel.OpenProcess(0x1000, False, pid or os.getpid())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        created, exited, kernel_time, user_time = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise ctypes.WinError(ctypes.get_last_error())
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 10000000 - 11644473600
    finally:
        kernel.CloseHandle(handle)
