# Verification status and scope

This candidate is under release verification. A passing local test is not an assertion
that an independent user has installed or exercised the product.

The public suite currently contains 100 tests covering source/config binding, capture
idempotence and conflict recovery, immutable evidence, scope filtering, model lifecycle,
maintenance queue ownership, PDF handling, project integration, Hook analysis and recovery.
Real stdio MCP tests exercise the registered tool contract and original-body reads.

An explicit long-maintenance test delays an owned test builder for 65 seconds, closes
the submitting MCP client, reconnects, observes completion and reads semantic evidence.
The delay exists only in the test fixture, not in production runtime options.

Performance evidence is kept separate:

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

Final gates still require a frozen release tree, fresh installation and update/rollback/
uninstall checks, installed native-client use and the independent receiver protocol.
