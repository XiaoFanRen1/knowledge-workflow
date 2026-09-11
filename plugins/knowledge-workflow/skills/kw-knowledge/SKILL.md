---
name: kw-knowledge
description: Consume and capture reusable knowledge in the user's explicitly configured private library. Use for evidence retrieval, knowledge notes, index maintenance, and restoration checks.
---

# Private knowledge lifecycle

The knowledge library belongs to the user. Resolve the project's logical
knowledge_ref through the local registry. Each configured MCP namespace is bound
to one library; do not guess a directory or search other libraries automatically.
If no binding exists, continue independent source work and report the missing binding.

When earlier knowledge can change the decision, search the bound library and read
the version-bound evidence. A hit, score or status flag is not proof. Preserve weak,
degraded, scope-ambiguous, historical and source-changed states in the answer.
Use next_cursor for long bodies; keep read_evidence max_chars within 1..6000.

Capture verified root causes, durable decisions, repeated traps and reusable
investigation chains. Do not capture incidental chatter or unsupported guesses as
established facts. Record the question, conclusion, sources, applicable scope,
verification and remaining uncertainty. Verification declarations remain declarations.

When capture is authorized, call capture_knowledge with a stable operation_id.
For updates, use the returned record_id and current expected hash; preserve human
edits on conflict. Normal authorized editing may maintain imported documents, but
capture_knowledge writes only its own managed notes.

After the batch is saved, call maintain_knowledge once. Keep the operation_id for
transport retries, then query maintenance_status until terminal. A maintenance
task can continue after the originating connection closes. Cancel only the named
task when cancellation is intended. A published result remains published.

Finish with search and read_evidence proving that the intended new body is indexed.
Saved-but-unindexed is a partial result. Do not silently report capture complete
when maintenance failed or the latest content was not covered by the generation.

Queries never install models, rebuild indexes, renew evidence dates, or promote
verification maturity. Feedback is an explicit observed outcome, not an automatic
success label. Never put private notes, traces, indexes, feedback or credentials in
the public workflow repository or upload them for diagnostics.
