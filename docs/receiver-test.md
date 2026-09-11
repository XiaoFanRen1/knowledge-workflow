# Independent Windows receiver check

This check uses synthetic information only. Do not send your knowledge documents,
credentials, databases, model caches or unreviewed machine logs to the maintainer.

1. Start from a Windows 11 account that has not used the author's workflow or paths.
   Record Windows build, Python version and Codex version. Install and sign in to your
   own Codex client, then extract the reviewed release bundle.
2. Run `install.cmd --dry-run`. Confirm the program/data/Codex paths and the startup
   choice are yours. Run `install.cmd --apply`; retain only the final success/failure
   status and elapsed time for reporting.
3. Open a fresh Codex task. Ask it to use the configured Knowledge Workflow library
   and save this **synthetic test statement**: “The orchard sensor clears its sample
   ring only after a confirmed reset acknowledgement.” State that knowledge capture
   and index maintenance are authorized.
4. Ask: “When may the orchard sensor erase its buffered measurements?” Require an
   actual knowledge search and original-text read. The result should cite the saved
   statement, keep verification declarations separate, and finish maintenance without
   asking you to repeat “continue”.
5. Create a temporary Python project with an existing AGENTS.md paragraph. Preview
   project initialization, apply it, and confirm your paragraph remains. No SDK or
   hardware should be required.
6. Upgrade with the next reviewed candidate or exercise the documented uninstall.
   Confirm that the synthetic knowledge and feedback remain in the private data folder.

Report the candidate version/commit, platform versions, which steps passed, and a
minimal redacted error if something failed. Installed configuration alone is not
proof of step 4. The stable release gate requires this check by an independent user;
local developer tests and a Windows Server CI runner do not replace it.
