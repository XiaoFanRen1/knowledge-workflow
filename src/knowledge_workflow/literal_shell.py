"""Literal shell parsing; unclassified substitutions are rejected."""

def segments(command: str) -> list[str]:
    """Split only unquoted control operators. Never rewrite the command."""
    result, buf, quote = [], [], ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote == "'" and command[i:i + 2] == "''":
            buf.extend("''")
            i += 2
            continue
        if ch == "`" and quote != "'":
            raise ValueError("shell escape or substitution cannot be classified")
        if command[i:i + 2] == "$(" and quote != "'":
            raise ValueError("shell substitution cannot be classified")
        if ch in "\"'":
            quote = "" if quote == ch else ch if not quote else quote
        if not quote and ch in ";|&\r\n":
            if "".join(buf).strip():
                result.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if quote:
        raise ValueError("unclosed shell quote")
    if "".join(buf).strip():
        result.append("".join(buf).strip())
    return result


def shell_tokens(command: str) -> list[tuple[str, bool]]:
    """Parse PowerShell literal quotes; bool marks unquoted redirection.

    shlex(posix=False) splits doubled single quotes into separate arguments.
    This parser preserves their literal apostrophe without rewriting commands.
    """
    result, buf, quote, started = [], [], "", False
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == quote and command[i:i + 2] == quote * 2:
                buf.append(ch)
                i += 2
                continue
            if ch == quote:
                quote = ""
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote, started = ch, True
        elif ch.isspace() or ch == ">":
            if started:
                result.append(("".join(buf), False))
                buf, started = [], False
            if ch == ">":
                operator = ">>" if command[i:i + 2] == ">>" else ">"
                result.append((operator, True))
                i += len(operator) - 1
        else:
            buf.append(ch)
            started = True
        i += 1
    if quote:
        raise ValueError("unclosed argument quote")
    if started:
        result.append(("".join(buf), False))
    return result


def tokens(command: str) -> list[str]:
    return [value for value, _ in shell_tokens(command)]


def redirects(command: str) -> list[str]:
    values = shell_tokens(command)
    targets = []
    for i, (value, operator) in enumerate(values):
        if operator:
            if i + 1 == len(values) or values[i + 1][1]:
                raise ValueError("redirection target missing")
            targets.append(values[i + 1][0])
    return targets
