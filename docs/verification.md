# Verification status and scope

This candidate is under release verification. A passing local test is not an assertion
that an independent user has installed or exercised the product.

The public suite currently contains 128 tests covering source/config binding, capture
idempotence and conflict recovery, immutable evidence, scope filtering, model lifecycle,
maintenance queue ownership, PDF handling, project integration, Hook analysis and recovery.
Real stdio MCP tests exercise the registered tool contract and original-body reads.

The first two Windows CI failures are retained as evidence. The first exposed a
test fixture comparing a Windows short temporary path with its resolved installation
owner path. The second was a reset PyPI connection before runtime installation.
Downloads now retry transient transport failures up to four attempts, remove their
partial files, and publish only bytes that match the pinned hash. Hash failures,
untrusted TLS certificates and permanent HTTP errors remain failures. Every native
command in a multi-command CI step is checked before proceeding.

An explicit long-maintenance test delays an owned test builder for 65 seconds, closes
the submitting MCP client, reconnects, observes completion and reads semantic evidence.
The delay exists only in the test fixture, not in production runtime options.

Performance evidence is kept separate:

Current measured results and negative samples are summarized in [performance verification](performance.md).

- Query comparisons must use the same generation, model files, thread settings and
  machine. Report cold time to the target body, warm distributions, semantic/degraded
  counts, starts and combined resource peaks. A lower lexical-fallback latency does
  not establish a semantic speed improvement.
- Maintenance comparisons include cold builds, forced cached rebuilds and unchanged
  maintenance. No-change maintenance skips model initialization/rechunking and does
  not republish the pointer. Changed parser/pipeline or source content invalidates that path.
- PDF acceptance checks page and original text, not only a path or ranking score.
- The reference constraints are warm query p95 <=3 seconds and corresponding peak
  RSS/private memory <=110% of baseline. Record sampling interval, sample count, corpus
  size and uncontrolled operating-system cache conditions.

The developer's private corpus and private questions are not distributed. Any aggregate
measurements derived from them must state that their raw corpus is private. Public
synthetic tests can be reproduced independently; Windows Server CI is not a Windows 11
desktop or independent receiver test.

Frozen RC3 installation and RC4 update/rollback/uninstall were exercised on Windows 11
with a separate Codex configuration home. Sixteen retained knowledge/index/model files
matched their original hashes; capture/job/feedback SQLite logical records were unchanged.
The real CLI-registered MCP read the same original evidence after update and rollback.
Uninstall removed the owned program and registrations. The remaining data restored into
an independent directory with eight snapshot files, two generations and one feedback-bound
body verified. Model files remain a separately supplied dependency.

The final candidate adds an uninstall precheck for user-added files at the program root.
Without it, cleanup removed owned program files before the final directory removal failed.
The regression test proves that the new check stops before deleting any program file;
the normal cleanup test proves that knowledge still remains outside the removed program.

A final local regression exposed a transient Windows sharing failure while reading
`current.json` during publication. JSON state reads and atomic JSON replacement now
allow up to 310 ms total backoff; permanent permission failures still raise, an exhausted
replacement retains the old pointer, and malformed JSON remains an error. A concurrent
reader/writer test verifies complete generations. Captured document bodies do not use
this internal replacement retry: their explicit retry rechecks the expected content hash.

These are isolated installation and actual stdio tests. Current desktop-plugin invocation
and the independent Windows receiver protocol remain separate acceptance gates. A release
candidate does not carry the stable-release claim until those gates are satisfied.

## RC6 activation in an existing native configuration

The author's RC6 activation failed after dependency/model/semantic checks. Native
`codex mcp add` omitted another stdio server's explicit empty `args = []`; the old
unowned-configuration hash treated that equivalent serialization as a substantive change.
The CLI also wrote an integer startup timeout as the same floating-point seconds value;
the old hash distinguished the numeric representations even though the values were equal.
A synthetic native CLI reproduction confirms that `mcp get` returns identical transport
settings before and after the omission. Nonempty arguments remain unchanged.

RC7 normalizes this empty stdio argument default and exact integral representations of
the two documented MCP timeout-in-seconds fields. It does not change timeout values.
HTTP or ambiguous transport arguments, nonempty arguments, booleans, unknown fields and
all other settings remain significant. Activation still rejects
actual unrelated edits. Recovery validates owned files/registrations, preserves unrelated
values present when recovery begins and verifies them again afterward. It neither writes
an old whole configuration back nor replaces the old recorded fingerprint.

The new release's recovery code runs with an existing verified dependency interpreter;
it does not delegate the repair back to defective old installed code. RC6's published tag
and assets are unchanged. Native desktop and independent receiver acceptance remain open.
