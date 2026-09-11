"""Delete only verified inactive program trees; knowledge directories are retained."""
import ctypes
import os
import shutil
from ctypes import wintypes
from pathlib import Path

from .distribution import sha256_file
from .util import atomic_json, read_json, reject_links


def active_processes(root):
    """Query executable paths only; never collect unrelated process command lines."""
    if os.name != "nt":
        raise RuntimeError("unsupported_cleanup_platform")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    found = []
    root = root.resolve()
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        success = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            handle = kernel.OpenProcess(0x1000, False, entry.pid)
            if handle:
                try:
                    size = wintypes.DWORD(32768)
                    path = ctypes.create_unicode_buffer(size.value)
                    if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                        if Path(path.value).resolve().is_relative_to(root):
                            found.append(entry.pid)
                finally:
                    kernel.CloseHandle(handle)
            success = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return found


def remove_verified_tree(path, root, expected):
    from .installation import tree
    root, path = reject_links(root).resolve(), reject_links(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("cleanup_target_outside_owned_root")
    if tree(path) != expected:
        raise ValueError("cleanup_target_modified")
    if active_processes(path):
        raise RuntimeError("cleanup_target_has_active_processes")
    shutil.rmtree(path)


def finish_uninstall(root):
    from .installation import tree
    root = reject_links(Path(root)).resolve()
    state = read_json(root / "installation.json")
    if state.get("state") != "unregistered" or str(root) != state["root"]:
        raise ValueError("uninstall_has_not_been_deactivated")
    active = active_processes(root)
    if active:
        return {"ok": False, "state": "cleanup_deferred", "active_pids": active, "data_retained": state["data"]}
    if tree(root / "marketplace") != state["marketplace_files"] or sha256_file(root / "kw.cmd") != state["launcher_sha256"]:
        raise ValueError("installed_program_files_modified")
    owned = []
    for directory in sorted((root / "versions").iterdir()):
        reject_links(directory)
        marker = read_json(directory / "preparation.json")
        if marker.get("state") != "prepared":
            raise ValueError("unfinished_version_requires_review")
        expected = marker["runtime_files"]
        if tree(directory / "venv") != expected:
            raise ValueError("installed_runtime_modified")
        if set(directory.iterdir()) != {directory / "venv", directory / "preparation.json"}:
            raise ValueError("unowned_version_content")
        owned.append((directory, expected))
    # Validate every target before the first deletion.
    for directory, expected in owned:
        remove_verified_tree(directory / "venv", root, expected)
        (directory / "preparation.json").unlink()
        directory.rmdir()
    (root / "versions").rmdir()
    remove_verified_tree(root / "marketplace", root, state["marketplace_files"])
    (root / "kw.cmd").unlink()
    state["state"] = "removed"
    atomic_json(root / "installation.json", state)
    result = {"ok": True, "state": "program_removed", "data_retained": state["data"],
              "transaction_records": str(root / "transactions")}
    atomic_json(Path(state["data"]) / "uninstall-report.json", result)
    return result
