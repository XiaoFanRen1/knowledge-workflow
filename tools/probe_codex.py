"""Native CLI configuration contract probe confined to an owned empty CODEX_HOME."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.codex_integration import Codex, PLUGIN

parser = argparse.ArgumentParser()
parser.add_argument("--codex", required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="kw-codex-contract-") as temp:
    codex = Codex(args.codex, Path(temp))
    versions = codex.capabilities()
    codex.call("plugin", "marketplace", "add", str(root), "--json")
    codex.call("plugin", "add", PLUGIN, "--json")
    codex.call("mcp", "add", "kw-contract-probe", "--", sys.executable, "--version")
    installed = codex.components(["kw-contract-probe"])
    codex.remove(["kw-contract-probe"])
    removed = codex.components(["kw-contract-probe"])
    print(json.dumps({"version": versions, "installed": installed, "removed": removed}, indent=2))
