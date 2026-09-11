# Knowledge Workflow

A project-neutral Codex workflow with a private local knowledge lifecycle: investigate,
implement, review, verify, capture reusable findings, maintain an index and read evidence back.

This is a development candidate. Final installation, performance and external receiver
acceptance have not yet been declared complete.

Initial target: Windows 11 x64, standard CPython 3.14 and Codex. Install and sign in to your
own Codex client first. Extract a release bundle and run `install.cmd --dry-run`, then
`install.cmd --apply` after reviewing the paths and per-user maintenance runner startup.
The installer uses an isolated Python environment and fixed dependency/model hashes.

The public repository contains code, instructions and synthetic examples. Your documents,
feedback, indexes, caches and private history are not part of the release. Selected evidence
used in an answer goes to your own Codex session. Uninstall retains knowledge data and models.

See [installation](docs/install.md), [privacy](docs/privacy.md), [architecture](docs/architecture.md)
and [third-party notices](THIRD_PARTY_NOTICES.md). No particular firmware SDK or editor is required.
