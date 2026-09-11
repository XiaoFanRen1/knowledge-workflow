# Windows installation and recovery

Use Windows 11 x64, standard CPython 3.14 and a compatible Codex executable. The initial
tested baseline is Python 3.14.2 / Codex CLI 0.153.4. Python and Codex are prerequisites;
the installer does not install system Python or change model/provider settings.

## Install a release

Extract the reviewed release package. Run from that directory:

```powershell
.\install.cmd --dry-run
.\install.cmd --apply
```

Defaults are `%LOCALAPPDATA%\KnowledgeWorkflow` for programs and
`%LOCALAPPDATA%\KnowledgeWorkflowData` for data. `--root`, `--data`, `--codex` and
`--codex-home` select explicit alternative paths. Existing unowned targets cause a conflict.
When using a CLI-only Codex installation, supply the actual compatible `codex.exe` path.

The bundle includes the fixed Windows wheelhouse. The installer verifies it, creates an
inactive version directory, installs dependencies, validates model hashes, and runs a
synthetic knowledge lifecycle before native registration. Progress continues during quiet
installation stages. A failed stage is not reported as complete.

For offline installation, provide `--offline --model-source <local-model-cache-or-snapshot>`.
Only the nine files in the model manifest are copied. No private knowledge is copied from
the model directory. A download failure does not silently reduce a full installation to
lexical-only retrieval.

## Maintenance runner and project Hook

Windows MCP clients may kill their complete child process tree on disconnect. An independent
local runner therefore owns maintenance tasks. The installer starts it from the user terminal
and, by default, prepares a per-user logon shortcut. It does not load models while idle.

`--no-startup` disables the logon shortcut. After logging in, start the registered runners:

```powershell
& '<installed kw.cmd path>' runner-registry --registry '<data directory>\registry.json'
```

The default installation creates no global safety Hook. `project-hook-config --root <project>`
prints an optional, machine-local Hook definition for review. Merge it through Codex's supported
Hook configuration and review `/hooks` before relying on it. Do not overwrite an existing Hook
file. Do not commit generated absolute machine paths to a shared project.
The project guard covers file edits and its declared shell/inline-Python contracts; other tools
remain subject to host permissions. It is not a Python sandbox or an authorization state engine.

## Project and knowledge operations

Use the installed `kw.cmd` path printed after installation. Commands include `doctor`,
`init`, `project-init`, `bind`, `resolve`, `search`, `read`, `capture`, `maintain`,
`maintenance-status`, `cancel`, `backup` and `restore`; each has `--help`.
The default new library configuration is `<data directory>\library\knowledge.json`.

An existing folder is registered explicitly with `init --source <folder>` in a NEW managed
knowledge directory. Review sources first. The command does not scan neighbouring folders.

For another library, use the installed launcher with explicit paths:

```powershell
& '<kw.cmd>' init --root '<new private library>' --model-dir '<verified model directory>' --source '<documents>'
& '<kw.cmd>' register-library --root '<program directory>' --config '<new private library>\knowledge.json' --knowledge-ref research
& '<kw.cmd>' bind --registry '<data directory>\registry.json' --project '<project>' --knowledge-ref project --kb-id '<returned library ID>'
```

Run registration from your terminal so its independent maintenance runner outlives the
MCP host. Registering creates a distinct MCP namespace; it does not silently switch another library.
Markdown, UTF-8 text and text PDFs are supported. Encrypted PDFs are rejected; scan-only PDFs
report an OCR requirement. Current extraction budgets are 10,000 source files and 64 MiB per file.

Save authorized notes using `capture --request <JSON file>`. The JSON carries `operation_id`,
`title`, `body`, source references, scope and verification declarations. Updates also require
`record_id` and `expected_hash`. Maintain after the batch, then search and read the returned
evidence ID. A saved note is not fully indexed until the read-back check succeeds.

## Update, rollback and uninstall

Run the NEW release installer against the same program/data/Codex paths. Each version has an
independent environment; user modifications and active maintenance prevent unsafe replacement.
The immediately preceding verified version is retained for rollback.

```powershell
.\install.cmd --rollback --root '<program directory>'
.\install.cmd --recover-pending --root '<program directory>'
.\install.cmd --uninstall --root '<program directory>'
```

`--rollback` uses a completed previous installation. `--recover-pending` handles an interrupted
installation transaction. Neither makes incompatible data readable by an old program; restore
the corresponding verified snapshot when a data-format migration requires it.

Uninstall removes owned registrations, startup and inactive program files. It retains knowledge,
feedback, model files and snapshots. Modified files and active versions are reported for review
instead of being forcibly deleted. Keep the release installer available until cleanup finishes.

`doctor --root <program directory>` checks local readiness. It does not prove a native client
actually invoked the tools. Open a fresh Codex task for installed-plugin and MCP acceptance.
