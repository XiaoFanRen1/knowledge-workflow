"""Opt-in deterministic project checks. No global install or workflow-state gates."""
import json
import re
import shlex
from pathlib import Path

from .util import contained, read_json


class Policy:
    def __init__(self, root, readonly):
        self.root = Path(root).resolve()
        self.readonly = [contained(self.root, value) for value in readonly]

    def protected(self, value, cwd, root=None):
        if any(c in str(value) for c in "$%*"):
            raise ValueError("dynamic write path cannot be classified")
        path = Path(value)
        target = (path if path.is_absolute() else Path(cwd) / path).resolve()
        return any(target == directory or target.is_relative_to(directory) for directory in self.readonly)

    def argv_issue(self, values, cwd, root=None, depth=0):
        if depth > 8 or not values or any(not isinstance(v, str) for v in values):
            return "command cannot be classified"
        name = Path(values[0]).stem.lower()
        if any(c in values[0] for c in "$%"):
            return "dynamic executable cannot be classified"
        if name in {"isd_download", "flash", "erase_vm", "burn", "remove-item", "rm", "rmdir", "del"}:
            return "destructive operation requires a reviewed user-terminal command"
        if name in {"python", "python3", "python3.14", "py"}:
            if "-c" not in values:
                return "script effects are outside inline analysis coverage"
            position = values.index("-c")
            if name == "py" and "-3.14" not in values[:position]:
                return "inline launcher must select Python 3.14 explicitly"
            if any(re.fullmatch(r"-3\.[0-9]+", value) and value != "-3.14" for value in values[:position]):
                return "inline interpreter version cannot be classified"
            if position + 1 >= len(values):
                return "Python source missing"
            from .python_safety import analyze
            result = analyze(values[position + 1], str(cwd), self.root, self.argv_issue, self.shell_issue, self.protected,
                             argv=["-c", *values[position + 2:]])
            return "" if result["decision"] == "allow" else result["decision"] + ": " + result["reason"]
        if name in {"powershell", "pwsh", "cmd", "bash", "sh"}:
            position = next((i for i, value in enumerate(values) if value.lower() in {"-c", "-command", "/c"}), None)
            if position is None or len(values) != position + 2:
                return "shell wrapper cannot be classified"
            return self.shell_issue(values[position + 1], cwd, root, depth + 1)
        if name == "git":
            index = 1
            while index < len(values) and values[index].startswith("-"):
                if values[index] == "-C" and index + 1 < len(values):
                    path = Path(values[index + 1])
                    cwd = (path if path.is_absolute() else Path(cwd) / path).resolve()
                    index += 2
                elif values[index] in {"--no-pager", "--no-optional-locks", "--literal-pathspecs"}:
                    index += 1
                else:
                    return "Git global option cannot be classified"
            if index == len(values):
                return "Git command missing"
            sub, rest = values[index], values[index + 1:]
            if sub in {"clean", "restore", "reset", "checkout"}:
                return "destructive Git operation requires review"
            if sub == "branch" and any(value in rest for value in {"-d", "-D", "--delete", "-f", "--force"}):
                return "destructive Git operation requires review"
            for i, token in enumerate(rest):
                if token == "--output" or token.startswith("--output="):
                    target = token.partition("=")[2] if "=" in token else rest[i + 1] if i + 1 < len(rest) else ""
                    if not target or self.protected(target, cwd):
                        return "output targets a project read-only path"
                if token.startswith(("--ext-diff", "--textconv")):
                    return "external Git processor cannot be classified"
            return "" if sub in {"status", "diff", "show", "log", "ls-files", "ls-tree", "rev-parse", "check-ignore", "branch"} else "Git mutation authorization belongs to the host workflow"
        if name == "rg" and any(value.startswith(("--pre", "--hostname-bin", "--search-zip")) for value in values[1:]):
            return "external search processor cannot be classified"
        if name in {"rg", "cat", "get-content", "get-childitem", "get-location", "pwd", "echo", "write-output"}:
            return ""
        return "unclassified executable"

    def shell_issue(self, command, cwd, root=None, depth=0):
        from .literal_shell import segments, tokens, redirects
        if depth > 8:
            return "shell nesting cannot be classified"
        working, stack = [str(Path(cwd).resolve())], []
        try:
            for part in segments(command):
                for target in redirects(part):
                    if any(self.protected(target, directory) for directory in working):
                        return "output targets a project read-only path"
                values = tokens(part)
                if not values:
                    continue
                name = values[0].lower()
                if name in {"cd", "chdir", "set-location", "sl", "push-location", "pushd"}:
                    arguments = values[1:]
                    if arguments and arguments[0].lower() in {"-path", "-literalpath"}:
                        arguments = arguments[1:]
                    if len(arguments) != 1 or any(c in arguments[0] for c in "$%*"):
                        return "working-directory change cannot be classified"
                    if name in {"push-location", "pushd"}:
                        stack.append(working[:])
                    working = list(dict.fromkeys([*working, *[str((Path(p) / arguments[0]).resolve()) for p in working]]))
                    if len(working) > 32:
                        return "working-directory branch budget exceeded"
                    continue
                if name in {"pop-location", "popd"}:
                    if len(values) != 1 or not stack:
                        return "directory stack cannot be classified"
                    working = stack.pop()
                    continue
                for directory in working:
                    reason = self.argv_issue(values, directory, root, depth + 1)
                    if reason:
                        return reason
            return ""
        except ValueError as exc:
            return str(exc)


def evaluate(root, event):
    root = Path(root).resolve()
    profile = read_json(root / ".knowledge-workflow/project.json")
    policy = Policy(root, profile["readonly"])
    name = event.get("tool_name", "")
    arguments = event.get("tool_input", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {"patch": arguments}
    reason = ""
    if name.endswith(("exec_command", "shell_command")):
        reason = policy.shell_issue(arguments.get("cmd", arguments.get("command", "")), arguments.get("workdir") or event.get("cwd") or root)
    elif name.endswith("apply_patch"):
        patch = arguments.get("patch", arguments.get("input", ""))
        paths = re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", patch, re.M)
        if not paths:
            reason = "patch targets cannot be classified"
        elif any(policy.protected(path, root) for path in paths):
            reason = "patch targets a project read-only path"
    elif name.endswith(("write_file", "edit_file")):
        path = arguments.get("path", arguments.get("file_path"))
        if not path:
            reason = "write target cannot be classified"
        elif policy.protected(path, root):
            reason = "write targets a project read-only path"
    else:
        return {"covered": False, "decision": "host_policy", "reason": "Tool is outside this project guard's declared coverage."}
    return {"covered": True, "decision": "deny" if reason else "no_prohibited_effect_detected", "reason": reason}


def hook(root, event):
    result = evaluate(root, event)
    if result["decision"] == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": result["reason"]}}
    # Do not override host approvals with an explicit permissionDecision=allow.
    return {}


def configuration(root):
    import subprocess
    from .processes import command
    root = Path(root).resolve()
    profile = read_json(root / ".knowledge-workflow/project.json")
    Policy(root, profile["readonly"])
    invocation = subprocess.list2cmdline(command("project-guard", "--root", root))
    return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": invocation}]}]}}
