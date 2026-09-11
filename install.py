"""Standard-library bootstrap. Use --dry-run before --apply.

The published release contains a hash-verified runtime wheel. No global Python
packages, model/provider settings, or global AGENTS file are modified.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def verify_release(bundle):
    manifest = json.loads((bundle / "release.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported_release")
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*")
              if p.is_file() and p.relative_to(bundle).as_posix() != "release.json"}
    if actual != set(manifest["files"]):
        raise ValueError("release_inventory_mismatch")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ":" in name or "\\" in name:
            raise ValueError("unsafe_release_path")
        path = bundle / relative
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise ValueError("linked_release_path")
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(block)
        if path.stat().st_size != expected["bytes"] or checksum.hexdigest() != expected["sha256"]:
            raise ValueError("release_hash_mismatch: " + name)
    return manifest


def discover_codex():
    root = Path(os.environ["LOCALAPPDATA"]) / "OpenAI/Codex/bin"
    candidates = sorted(root.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise ValueError("Codex executable not found; supply --codex with its absolute .exe path")
    return candidates[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KnowledgeWorkflow")
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KnowledgeWorkflowData")
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--model-source", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-startup", action="store_true", help="Do not create the per-user logon shortcut; start the runner manually after login")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--recover-pending", action="store_true")
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 14):
        raise ValueError("v1 requires Windows x64 and standard CPython 3.14")
    import struct
    import sysconfig
    if struct.calcsize("P") != 8 or sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise ValueError("v1 requires the standard GIL-enabled x64 interpreter")
    bundle = args.bundle.resolve()
    manifest = verify_release(bundle)
    wheel = bundle / manifest["runtime_wheel"]
    if wheel.suffix != ".whl" or not wheel.is_relative_to(bundle):
        raise ValueError("invalid_runtime_wheel")
    # The wheel is now verified. Its bootstrap modules depend only on the stdlib.
    sys.path.insert(0, str(wheel))
    from knowledge_workflow.installation import preview, prepare
    from knowledge_workflow.command_runner import run
    codex = args.codex or discover_codex()
    if args.recover_pending or args.uninstall or args.rollback:
        if args.uninstall and not args.root.exists():
            print(json.dumps({"ok": True, "state": "already_removed", "data_policy": "retained"}))
            return 0
        state_file = args.root / ("pending-installation.json" if args.recover_pending else "installation.json")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        active = state["prepared"] if args.recover_pending else state
        action = "recover-install" if args.recover_pending else "rollback-install" if args.rollback else "deactivate-install"
        if args.uninstall and state.get("state") == "removed":
            print(json.dumps({"ok": True, "state": "already_removed", "data_retained": state["data"]}))
            return 0
        print(run([active["python"], "-I", "-B", "-X", "utf8", active["entry"], action, "--root", args.root], phase=action, timeout=900))
        if args.uninstall:
            from knowledge_workflow.cleanup import finish_uninstall
            result = finish_uninstall(args.root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        return 0
    proposed = preview(bundle, args.root, args.data, codex, args.codex_home, startup=not args.no_startup)
    print(json.dumps(proposed, ensure_ascii=False, indent=2), flush=True)
    if not args.apply:
        return 0
    prepared = prepare(bundle, args.root, args.data, model_source=args.model_source, offline=args.offline)
    record = Path(prepared["python"]).parents[2] / "preparation.json"
    command = [prepared["python"], "-I", "-B", "-X", "utf8", prepared["entry"], "activate-install",
               "--bundle", bundle, "--root", args.root, "--data", args.data,
               "--codex", codex, "--codex-home", args.codex_home, "--prepared", record]
    if args.no_startup:
        command.append("--no-startup")
    print(run(command, phase="Activate verified local workflow", timeout=180))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}), file=sys.stderr)
        raise SystemExit(1)
