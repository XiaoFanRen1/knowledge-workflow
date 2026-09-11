# Performance verification — 2026-09-11

The measured query latency and process-memory ceilings passed the stated comparison
constraints. This does not establish that every operation is faster than the baseline.
The baseline is the pre-publicization local knowledge service; its private corpus and
questions are not distributed. Synthetic source tests contain no private knowledge.

## Environment and limits

- Windows 11 x64, build 26200; Intel Core i5-14400; approximately 16 GiB RAM.
- Standard CPython 3.14.2; one MCP client per variant; CPU model inference with four
  threads, OpenBLAS limited to one thread, no reranker.
- The pinned multilingual MiniLM model and revision declared in `assets/model.json`.
- Alternating baseline/candidate runs on the same machine. Operating-system file caches
  and other host applications were not controlled. Memory sampled every 100 ms, except
  the separate no-change check, where both variants used 10 ms samples.
- Results below describe the tested workloads, not a statistical guarantee for another
  machine, library size, concurrent-client count or future dependency version.

## Query comparison on a private fixed generation

Each variant ran 12 new-process semantic queries across three question classes, followed
by 36 warm requests. Both completed semantics 12/12, had no first-response fallback in
this sample, started 12 workers and repeated initialization zero times. All 12 paired
original bodies and source states matched. Weak machine labels were retained.

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Time to target semantic body, p50 | 7.578 s | 8.017 s |
| Time to target semantic body, p95 | 28.753 s | 10.303 s |
| Warm query p50 | 0.301 s | 0.249 s |
| Warm query p95 | 0.370 s | 0.286 s |
| Service and worker peak RSS | 1.238 GB | 1.230 GB |
| Service and worker peak private memory | 1.726 GB | 1.727 GB |

The baseline had one large cold-start outlier. Its smaller candidate p95 must not be
presented as a proven general cold-start gain; candidate cold p50 was slower. The warm
p95 constraint of three seconds and the 110% memory ceiling passed for these samples.

## Maintenance and PDF extraction on synthetic sources

The corpus contains 80 synthetic Markdown records and one 12-page text PDF: 81 sources
and 492 distinct embedding keys. Three paired rounds exercised cold builds, cached
rebuilds and unchanged maintenance.

| End-to-end p50 | Baseline | Candidate |
| --- | ---: | ---: |
| Cold build | 17.751 s | 17.855 s |
| Forced cached rebuild | 8.518 s | 8.748 s |
| Unchanged maintenance, after import fix | 0.245 s | 0.261 s |

Both variants encoded 492 keys on each cold build. Cached and unchanged runs encoded
zero keys and restored zero historical vectors. Cold/cached process-memory peaks met
the 110% ceiling. The first no-change candidate exceeded its private-memory ceiling;
unnecessary imports were removed and a new paired run passed: peak RSS 29.84/28.30 MB
and private memory 17.22/16.54 MB (baseline/candidate). The failed run was retained.

All 12 PDF page texts matched in each of three parser comparisons after whitespace
normalization. Separate tests check readable original text, page numbers, source
references, scan-only PDFs and broken PDFs. Scans and parsing failures cannot silently
publish an empty index over a valid generation.

## Maintenance with an already resident semantic worker

Three additional alternating pairs include the MCP service, its resident semantic worker
and the maintenance process tree. The candidate additionally includes its independent
runner and task supervisors. Before the first maintenance, only the isolated test vector
cache was emptied; the source bytes and historical generation remained intact.

| Workload | Baseline p50 | Candidate p50 | Candidate/baseline peak RSS | Peak private memory ratio |
| --- | ---: | ---: | ---: | ---: |
| Recover history into the missing vector cache | 8.991 s | 8.684 s | 104.49% | 101.32% |
| Explicit cached rebuild immediately afterward | 8.844 s | 8.898 s | 103.81% | 102.20% |

The first candidate workload uses the real MCP maintenance task. The baseline has no
equivalent task API and uses its explicit maintenance CLI. The repeated forced rebuild
uses explicit CLIs for both variants, with the MCP reader still resident. Each recovery
restored exactly 492 keys once and encoded zero; every repeated rebuild restored and
encoded zero. Every reader started one worker. Both aggregate memory ceilings passed.

Setup also retains a quality counterexample: the old ranker confused two near-identical
synthetic sensor identifiers in all three runs. The candidate returned the requested
identifier's original body in all three. This small counterexample complements the
private paired-body check; it is not a broad accuracy benchmark.

Preparation failures are not included as successful measurements. An initial harness
incorrectly required the first response to be semantic even while initialization was
still continuing. Another harness attempt appended a newline while expecting zero
new encoding; that changed one embedding input and correctly encoded one key. The
accepted cache-loss comparison leaves source bytes unchanged and records the actual
query preparation phase separately from maintenance timing.

## Release boundary

The measured build, parser, model, retrieval and worker code is unchanged by the final
installer transport and uninstall precheck fixes. After adding the bounded JSON-state
sharing retry, the combined-process table above was rerun with the final runtime. A
separate sequential no-change recheck also passed: baseline/candidate p50 0.270/0.251 s,
peak RSS 29.95/28.51 MB and private memory 17.20/16.80 MB. An earlier diagnostic rerun
overlapped a test-cache signature refresh and was retained but excluded from these final
timing results. No algorithm threshold or model deadline was expanded.

The pinned model, dependency wheel
hashes and original private service tests are checked separately. Installation, native
desktop invocation and independent receiver acceptance retain their own verification
states; these measurements do not substitute for them.
