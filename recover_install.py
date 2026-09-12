"""Run the verified new recovery implementation with an existing dependency runtime."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from install import verify_release
    manifest = verify_release(bundle)
    wheel = bundle / manifest["runtime_wheel"]
    if wheel.suffix != ".whl" or not wheel.is_relative_to(bundle):
        raise ValueError("invalid_runtime_wheel")
    if "knowledge_workflow" in sys.modules:
        raise RuntimeError("recovery_runtime_already_bound")
    sys.path.insert(0, str(wheel))
    from knowledge_workflow import __version__
    from knowledge_workflow.installation import recover_pending
    result = recover_pending(args.root)
    print(json.dumps({**result, "recovery_code_version": __version__, "recovery_commit": manifest["commit"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": type(error).__name__, "message": str(error)}}), file=sys.stderr)
        raise SystemExit(1)
