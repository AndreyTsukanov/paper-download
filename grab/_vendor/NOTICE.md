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

If you re-vendor a newer upstream, re-apply these seven changes.
