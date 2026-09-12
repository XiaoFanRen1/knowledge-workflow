"""Register owned components through the supported Codex CLI only."""
import os
import copy
import subprocess
import re
import tomllib
from pathlib import Path

from .command_runner import run
from .util import canonical, digest, read_json, reject_links

MARKETPLACE = "knowledge-workflow"
PLUGIN = "knowledge-workflow@knowledge-workflow"


class Codex:
    def __init__(self, executable: Path, home: Path):
        self.executable = reject_links(executable).resolve()
        self.home = reject_links(home).resolve()
        if not self.executable.is_file() or self.executable.suffix.lower() != ".exe":
            raise ValueError("explicit_codex_executable_required")
        self.env = {**os.environ, "CODEX_HOME": str(self.home)}

    def call(self, *args):
        return run([self.executable, *args], phase="Codex " + " ".join(args[:2]), env=self.env, timeout=60)

    def capabilities(self):
        output = self.call("--version")
        match = re.search(r"codex-cli\s+([0-9.]+)", output)
        if not match:
            raise ValueError("unsupported_codex_version_response")
        version = match.group(1)
        for args in (("plugin", "marketplace", "add", "--help"), ("plugin", "add", "--help"), ("mcp", "add", "--help")):
            self.call(*args)
        return version

    def config(self):
        path = self.home / "config.toml"
        return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def components(self, names):
        config = self.config()
        return {"marketplace": config.get("marketplaces", {}).get(MARKETPLACE),
                "plugin": config.get("plugins", {}).get(PLUGIN),
                "mcp": {name: config.get("mcp_servers", {}).get(name) for name in names}}

    def unowned_hash(self, names, *, version=2):
        if version not in (1, 2):
            raise ValueError("unsupported_configuration_fingerprint")
        config = copy.deepcopy(self.config())
        for table, keys in (("marketplaces", [MARKETPLACE]), ("plugins", [PLUGIN]), ("mcp_servers", names)):
            value = config.get(table, {})
            for key in keys:
                value.pop(key, None)
            if not value:
                config.pop(table, None)
        if version == 2:
            # Native `codex mcp add` omits empty stdio args while rewriting its
            # server table. The native reader resolves omitted and [] identically.
            # HTTP/ambiguous transport args and other default fields stay distinct.
            for server in config.get("mcp_servers", {}).values():
                if (isinstance(server, dict) and isinstance(server.get("command"), str)
                        and server["command"] and "url" not in server and server.get("args") == []):
                    server.pop("args")
                if isinstance(server, dict):
                    # The CLI also serializes seconds as floats (17 -> 17.0).
                    # Canonicalize only these documented numeric duration fields,
                    # preserving their exact value and distinguishing bools/unknowns.
                    for key in ("startup_timeout_sec", "tool_timeout_sec"):
                        seconds = server.get(key)
                        if type(seconds) is float and seconds.is_integer() and 0 <= seconds <= 2 ** 53:
                            server[key] = int(seconds)
        return digest(canonical(config))

    def register(self, marketplace_root, python, entry, libraries):
        self.home.mkdir(parents=True, exist_ok=True)
        self.call("plugin", "marketplace", "add", str(marketplace_root), "--json")
        self.call("plugin", "add", PLUGIN, "--json")
        for item in libraries:
            self.call("mcp", "add", item["name"], "--", str(python), "-I", "-B", "-X", "utf8",
                      str(entry), "mcp", "--config", item["config"])

    def remove(self, names):
        for name in names:
            if self.components(names)["mcp"].get(name) is not None:
                self.call("mcp", "remove", name)
        if self.components(names)["plugin"] is not None:
            self.call("plugin", "remove", PLUGIN, "--json")
        if self.components(names)["marketplace"] is not None:
            self.call("plugin", "marketplace", "remove", MARKETPLACE, "--json")
