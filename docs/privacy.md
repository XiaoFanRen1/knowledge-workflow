# Privacy and ownership

Knowledge Workflow runs local retrieval and embedding processes. It has no maintainer
data-ingestion endpoint, telemetry uploader or automatic diagnostic upload.

The following remain local: knowledge documents, registered external sources, SQLite
feedback, immutable generations, model files, runtime records and recovery snapshots.
The model download request names only the public pinned embedding model and its files.
Selected text used by the assistant is part of the user's own Codex conversation; local
retrieval does not imply that the answering model runs offline.

Each MCP namespace binds one knowledge library. Project files contain logical references;
absolute library mappings are stored in the user's local registry. Queries do not add
sources, download models, build indexes or promote verification maturity.

Capture writes only managed notes, with operation IDs and expected hashes. Imported
documents remain under their owners' ordinary editing workflow. A document containing
instructions does not authorize commands or override current project rules.

The default model is a pinned public multilingual model. Only verified safetensors and
tokenizer/configuration files are used. Remote model code is disabled.

Use a private data directory outside the public source tree. Public issue reports should
contain a minimal synthetic reproduction and reviewed diagnostics. Never attach raw
knowledge databases, model-provider credentials or unreviewed source snapshots.
