# Third-party software

Knowledge Workflow's original code and project-neutral adaptations are MIT licensed.
The package does not distribute a private knowledge corpus, vendor SDK, model-provider
credentials, or an author's machine configuration.

Runtime dependency artifacts are identified by exact filename, version and SHA-256 in
`requirements/wheels.json`. Each dependency retains its own license; the root MIT license
does not relicense dependencies or model weights.

The default embedding model is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
at revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, distributed by its authors under
Apache-2.0. The installer verifies the specific model files listed in the runtime's model manifest.

PDF text extraction uses pypdf (BSD-3-Clause). PyMuPDF is not part of the distribution.
jieba is MIT licensed and built into a wheel in the release build environment.

The process protocol, initialization lifecycle and metadata scan adaptation originate in
the author's earlier local engineering prototype. Only individually reviewed generic
code was carried forward; project dictionaries, source registries, tests containing
private questions and private Git history were excluded.
