"""An explicitly previewed per-user startup shortcut, never a global Hook."""
import os
import subprocess
from pathlib import Path

from .distribution import sha256_file
from .util import reject_links


def target_path():
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/KnowledgeWorkflow.lnk"


def install(python, entry, registry, *, target=None, expected_hash=None):
    import win32com.client
    target = reject_links(target or target_path()).resolve()
    if target.exists() and (expected_hash is None or sha256_file(target) != expected_hash):
        raise ValueError("startup_shortcut_modified_or_unowned")
    target.parent.mkdir(parents=True, exist_ok=True)
    shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(target))
    shortcut.TargetPath = str(Path(python).with_name("pythonw.exe"))
    shortcut.Arguments = subprocess.list2cmdline(["-I", "-B", "-X", "utf8", str(entry), "runner-registry", "--registry", str(registry)])
    shortcut.WorkingDirectory = str(Path(python).parent)
    shortcut.WindowStyle = 7
    shortcut.Description = "Start local Knowledge Workflow maintenance runners; no model is loaded."
    shortcut.Save()
    return {"path": str(target), "sha256": sha256_file(target)}


def remove(record):
    target = reject_links(Path(record["path"])).resolve()
    if target.exists():
        if sha256_file(target) != record["sha256"]:
            raise ValueError("startup_shortcut_modified")
        target.unlink()
