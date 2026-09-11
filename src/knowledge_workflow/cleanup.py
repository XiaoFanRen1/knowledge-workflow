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


def transaction_inventory(root, *, retain=None):
    from .installation import tree
    directory = root / "transactions"
    results = []
    if not directory.exists():
        return results
    for tx in sorted(directory.iterdir()):
        reject_links(tx)
        if retain and tx.resolve() == retain.resolve():
            continue
        receipt = read_json(tx / "receipt.json")
        if receipt.get("phase") not in {"installed", "recovered", "rolled_back"}:
            raise ValueError("unfinished_transaction_requires_review")
        expected = receipt.get("owned_files")
        if expected is None:
            raise ValueError("transaction_inventory_not_recorded")
        actual = tree(tx)
        without_receipt = {k: v for k, v in actual.items() if k != "receipt.json"}
        if without_receipt != expected:
            raise ValueError("transaction_files_modified")
        results.append((tx, actual, receipt))
    return results


def prune(root):
    """Keep current and one rollback version; defer versions with active processes."""
    from .installation import tree
    root = reject_links(Path(root)).resolve()
    state = read_json(root / "installation.json")
    if state.get("state") != "installed" or (root / "pending-installation.json").exists():
        raise ValueError("installation_not_ready_for_pruning")
    keep = {state["version"]}
    if state.get("previous"):
        keep.add(state["previous"]["version"])
    removed, deferred = [], []
    for directory in sorted((root / "versions").iterdir()):
        if directory.name in keep:
            continue
        marker = read_json(directory / "preparation.json")
        if active_processes(directory):
            deferred.append(directory.name)
            continue
        if marker.get("state") != "prepared" or tree(directory / "venv") != marker["runtime_files"]:
            raise ValueError("obsolete_runtime_modified")
        remove_verified_tree(directory / "venv", root, marker["runtime_files"])
        (directory / "preparation.json").unlink()
        directory.rmdir()
        removed.append(directory.name)
    retain = Path(state["rollback_material"]) if state.get("previous") and state.get("rollback_material") else None
    history = Path(state["data"]) / "installation-history"
    history.mkdir(parents=True, exist_ok=True)
    for tx, expected, receipt in transaction_inventory(root, retain=retain):
        atomic_json(history / (tx.name + ".json"), {"phase": receipt["phase"],
            "version": receipt["prepared"]["version"], "commit": receipt["prepared"]["commit"]})
        remove_verified_tree(tx, root, expected)
    return {"ok": True, "removed_versions": removed, "deferred_busy_versions": deferred}


def finish_uninstall(root):
    from .installation import tree
    root = reject_links(Path(root)).resolve()
    state = read_json(root / "installation.json")
    if state.get("state") != "unregistered" or str(root) != state["root"]:
        raise ValueError("uninstall_has_not_been_deactivated")
    allowed = {"installation.json", "installation.lock", ".knowledge-workflow-owner.json",
               "versions", "marketplace", "kw.cmd", "transactions"}
    if any(path.name not in allowed for path in root.iterdir()):
        raise ValueError("unowned_program_content: review added files before uninstalling")
    owner = read_json(root / ".knowledge-workflow-owner.json")
    if owner != {"schema_version": 1, "product": "knowledge-workflow", "root": str(root), "data": state["data"]}:
        raise ValueError("installation_owner_mismatch")
    active = active_processes(root)
    if active:
        return {"ok": False, "state": "cleanup_deferred", "active_pids": active, "data_retained": state["data"]}
    if tree(root / "marketplace") != state["marketplace_files"] or sha256_file(root / "kw.cmd") != state["launcher_sha256"]:
        raise ValueError("installed_program_files_modified")
    transactions = transaction_inventory(root)
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
    history = Path(state["data"]) / "installation-history"
    history.mkdir(parents=True, exist_ok=True)
    for tx, expected, receipt in transactions:
        atomic_json(history / (tx.name + ".json"), {"phase": receipt["phase"],
            "version": receipt["prepared"]["version"], "commit": receipt["prepared"]["commit"]})
        remove_verified_tree(tx, root, expected)
    if (root / "transactions").exists():
        (root / "transactions").rmdir()
    (root / "installation.lock").unlink(missing_ok=True)
    (root / ".knowledge-workflow-owner.json").unlink(missing_ok=True)
    (root / "installation.json").unlink()
    root.rmdir()
    result = {"ok": True, "state": "program_removed", "data_retained": state["data"],
              "transaction_records": str(history)}
    atomic_json(Path(state["data"]) / "uninstall-report.json", result)
    return result
