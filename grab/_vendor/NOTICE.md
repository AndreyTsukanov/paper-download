# Vendored third-party code

`paper_search_mcp/` is a copy of the **paper-search-mcp** package, version **0.1.4**,
included here so `grab` runs without requiring the package to be pip-installed.

- Upstream: https://pypi.org/project/paper-search-mcp/ (repo: https://github.com/openags/paper-search-mcp)
- License: MIT (see `paper_search_mcp/LICENSE`)

It still imports its own third-party dependencies (`requests`, `httpx`, `beautifulsoup4`,
`lxml`, `pypdf`, `feedparser`, `mcp`, `fastmcp`); these are declared in the project's
`pyproject.toml`.

## Modifications (all in `paper_search_mcp/server.py`)

The OA-fallback chain (`download_with_fallback`, `_try_repository_fallback`,
`_download_from_url`) is upstream. The following diverge from upstream 0.1.4:

- **`_is_likely_paywalled` + smart routing** — for DOIs from known paywalled publishers,
  skip the OA repository/Unpaywall chain and go straight to Sci-Hub. *(Pre-existing local
  change; not upstream.)*
- **`_is_valid_pdf`** — validates the primary download result before accepting it.
  *(Pre-existing local change; not upstream.)*
- **DOI-match gate in `_try_repository_fallback`** — new helpers `_normalize_doi`,
  `_title_similar`, `_candidate_matches`, and a check that only downloads a repository
  candidate whose DOI (or, lacking a DOI, title) matches the requested paper. Upstream
  downloads the first result with any `pdf_url`, which silently saves a topically-similar
  but **wrong** paper for anything not in OA. *(Added for `grab`.)*
- **Landing-page PDF extraction in `_download_from_url`** — new helpers `_is_pdf_response`
  and `_extract_pdf_link`. When a resolved OA URL returns HTML instead of a PDF, parse it
  for a PDF link (`citation_pdf_url` meta, DSpace bitstream/`download`, or a `.pdf` href)
  and fetch that once. Upstream rejects any non-PDF response outright, missing OA copies
  that sit behind an institutional-repository landing page. *(Added for `grab`.)*
- **`NotImplementedError` from a primary downloader is treated as "no direct PDF (expected)"**
  in `download_with_fallback`, not as a failure. Connectors like CrossRef are metadata
  registries that raise `NotImplementedError` from `download_pdf` *by design*; upstream logs
  this as a `WARNING: Primary download failed …` and appends the constant message to the
  error list, which looks like a real error and obscures the OA chain that actually fetches
  the PDF (Unpaywall/repositories). We now log it at `debug` and skip adding it to
  `attempt_errors`. *(Added for `grab`.)*
- **All Unpaywall OA locations are tried, repository-first.** New method
  `UnpaywallResolver.resolve_oa_pdf_urls` (in `academic_platforms/unpaywall.py`, beside the
  unchanged `resolve_best_pdf_url`) returns *every* OA URL from `best_oa_location` +
  `oa_locations`, ordered `host_type=="repository"` first, de-duplicated. `download_with_fallback`
  now loops over that list (instead of trying only the first URL once). Rationale: Unpaywall
  frequently lists a publisher `bronze` link that 403s alongside a downloadable repository
  copy; upstream only tried the first and gave up. DOI-gate + content-verify still backstop
  a wrong grab. *(Added for `grab`.)*
- **Direct-first, proxy-fallback PDF download.** `_download_from_url` was split into
  `_download_from_url_once(url, output_path, proxy=None)` (one attempt, with the landing-page
  logic) and `_download_from_url`, which tries a **direct** connection first and retries via
  `os.environ["GRAB_DOWNLOAD_PROXY"]` **only if direct fails**. Some institutional repositories
  (e.g. `air.unimi.it`) are reachable only through the proxy while most hosts and all the
  search/metadata APIs work direct — so the proxy stays scoped to the final file fetch and is
  never the default path. `cli.py` sets `GRAB_DOWNLOAD_PROXY` from the `.env` proxy (the same
  value it captures for the OpenAI client) before stripping the standard proxy vars. *(Added for `grab`.)*

- **OpenAlex + Crossref as independent OA-location providers (by exact DOI).** New method
  `OpenAlexSearcher.resolve_oa_pdf_urls(doi)` (in `academic_platforms/openalex.py`) looks the work
  up by exact DOI (`works/https://doi.org/<doi>`) and returns every `locations[].pdf_url` +
  `best_oa_location` + `open_access.oa_url`, repository-version first, de-duplicated (it reads
  `UNPAYWALL_EMAIL` for the polite pool and an optional `OPENALEX_API_KEY`). `download_with_fallback`
  gained a block (after the Unpaywall loop, before Sci-Hub) that tries those URLs plus Crossref's
  exact-DOI `link[]` PDF (`crossref_searcher.get_paper_by_doi(doi).pdf_url`) via `_download_from_url`.
  Rationale: OpenAlex is an independent OA index that often has a green-OA copy Unpaywall missed
  (e.g. an institutional-repository handle). Exact-DOI lookup means no fuzzy DOI-gate is needed;
  content-verify still backstops. *(Added for `grab` — Track 2.)*

- **Fatcat / IA Scholar as a legal OA-location provider (by exact DOI).** New connector
  `academic_platforms/fatcat.py` (`FatcatResolver.resolve_pdf_urls(doi)`) → Fatcat release lookup
  (`/v0/release/lookup?doi=…&expand=files`), returns preserved PDF URLs (archive.org / web.archive.org
  first), deduped. Wired into `download_with_fallback` via the new `_try_fatcat` helper, in the **legal**
  chain after OpenAlex/Crossref (and, in the paywalled-publisher shortcut, before any shadow source so a
  legal preserved copy is preferred). Always on, no flag. Graceful skip on error. *(Added for `grab` — Track 3.)*
- **Generalized shadow fallback: Sci-Hub → SciDB → Nexus, behind one opt-in list.** `download_with_fallback`
  gained a `shadow_sources: List[str]` param and a `_try_shadow_sources` helper that loops the enabled
  shadow connectors in order; `_shadow_fetcher` dispatches `scihub`→`SciHubFetcher` (unchanged),
  `scidb`→`SciDBFetcher` (new `academic_platforms/scidb.py`, Anna's Archive `…/scidb/<doi>`, `GRAB_SCIDB_URL`),
  `nexus`→`NexusFetcher` (new `academic_platforms/nexus.py`, experimental IPFS/STC, `GRAB_NEXUS_GATEWAY`).
  The single Sci-Hub tail and the `_is_likely_paywalled` shortcut now both call `_try_shadow_sources`.
  **Backward-compatible:** `use_scihub`/`scihub_base_url` params are kept; `use_scihub=True` with no list
  maps to `["scihub"]`, so prior behaviour is identical. Shadow sources are off by default; every shadow
  PDF is still structurally checked here and content-verified by the pipeline. *(Added for `grab` — Track 4.)*

**Copyright note:** the shadow sources (Sci-Hub, SciDB/Anna's Archive, Nexus/STC) serve paywalled
content; they are off by default and enabled only by an explicit user opt-in (`--scihub` / `--shadow`).
Use is the user's responsibility.

- **Centralized, env-tunable network budgets + bounded Semantic Scholar.** `config.py` gained a block
  of grab tunables (read via `get_env`, override in `.env`): `SEMANTIC_TIMEOUT`/`SEMANTIC_MAX_RETRIES`/
  `SEMANTIC_RETRY_DELAY`, `FATCAT_CONNECT_TIMEOUT`/`FATCAT_READ_TIMEOUT`/`FATCAT_FAILURE_LIMIT`,
  `SCIDB_TIMEOUT`, `NEXUS_TIMEOUT`. `semantic.py::request_api` now reads `max_retries`/`retry_delay`/
  `timeout` from those (was hardcoded `3` / `5`s / `30`s) — unauthenticated Semantic Scholar 429-storms
  were the dominant time-sink on citation batches; it now fails fast (1 attempt, 10 s) instead of
  stalling ~25–45 s/paper, with negligible recall impact (Crossref/arXiv answer anyway). `fatcat.py`
  reads its short timeouts + the circuit-breaker threshold from config (after `FATCAT_FAILURE_LIMIT`
  consecutive connection failures it disables itself for the run, so a down api.fatcat.wiki costs
  seconds, not minutes); `scidb.py`/`nexus.py` read their `*_TIMEOUT`. *(Added for `grab` — perf pass.)*

- **Per-source chain trace.** `download_with_fallback` takes an optional `trace: List[str]` and records a
  `source:outcome` token at every stage (`unpaywall:none`, `openalex:403`, `crossref:none`, `fatcat:down`,
  `scihub:not found`, `scidb:unreachable`, `nexus:skipped (no GRAB_NEXUS_GATEWAY)`, …) via the `_rec`
  helper; `_try_shadow_sources`/`_try_fatcat` fill it too. To report honest shadow reasons, `scidb.py` and
  `nexus.py` set a `last_status` attribute, and `fatcat.py` tracks `_last_unreachable` (so "down" =
  connection failure is distinguished from "none" = reachable-but-no-PDF). The pipeline surfaces the trace
  as `result['chain']` (printed as a `chain:` line and stored in the manifest). *(Added for `grab` —
  visibility pass.)*

Note: logging configuration (verbose library logs by default) and `full_run.log` live in `grab/cli.py`
(not vendored); the chain `chain:` line + manifest field live in `grab/pipeline.py`/`cli.py`.

If you re-vendor a newer upstream, re-apply these twelve changes (they touch `openalex.py`, `server.py`,
`config.py`, `semantic.py`, and the new `fatcat.py`/`scidb.py`/`nexus.py` connectors).
