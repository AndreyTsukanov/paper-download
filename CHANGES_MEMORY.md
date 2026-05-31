# CHANGES_MEMORY.md — detailed change history & rationale

Imported by `CLAUDE.md` via `@CHANGES_MEMORY.md`. Its purpose: let a future session
reach the same quality **without** replaying the whole multi-hour transcript. It records
what was broken, what we changed, **why**, and how we verified it. Update it when you make
another substantive change.

---

## 0. Starting point (what existed before this work)

`grab` downloads one specific paper (DOI/arXiv/PMID/title), OA-first, reusing the **vendored**
`paper_search_mcp` download chain directly (no agent). On the `hermes` branch someone had
already: vendored the package, added OpenAI as an LLM provider, and patched the vendored
`server.py` with two helpers — `_is_likely_paywalled` (route known-paywalled DOIs straight to
Sci-Hub, skipping the OA chain) and `_is_valid_pdf` (structural PDF check on the primary
download). These were **not** in upstream.

A batch run on `tests/test_papers.txt` (19 head-&-neck oncology citations, with duplicates)
produced `tests/downloads/` + `manifest.jsonl` + `run_oa.log` / `run_scihub.log`. The user
markitdown-converted the PDFs and renamed them by their *actual* content.

## 1. Symptoms the user reported

1. `sci-hub.se` did not work (needed `.ru`).
2. The downloaded files did **not** match the requested papers.

## 2. Investigation findings (the real diagnosis)

- **The "names don't match" was much worse than a naming bug: it was wrong-content saved as ✓.**
  Of 13 OA "successes" in the manifest, **only 1 was the requested paper** (Chow/KEYNOTE-012).
  Example: the clean DOI for Cohen/KEYNOTE-040 returned a Ghana birth-weight paper. Two distinct
  requests ([1] Vermorken EXTREME, [2] Guigay TPEx) were even saved to the *same* file.

- **Root cause — upstream, not hermes:** `server.py::_try_repository_fallback` keyword-searches
  CORE/EuropePMC/OpenAIRE/PMC by DOI-or-title and downloads the **first result with any
  `pdf_url`, with no check that it is the requested paper**. For papers not in OA it grabs a
  topically-similar but wrong PDF and reports success. Confirmed identical in upstream
  `github.com/openags/paper-search-mcp` `main` (so the skill, which runs the same code via MCP,
  has the same bug — it's only masked there by the agent-in-the-loop eyeballing results).

- **The disambiguation LLM was NOT the problem.** `manifest.matched.doi` was correct in ~12/13
  (gpt-4.1-mini picked the right DOI). The download step then ignored that DOI. So "why can't a
  small model do it?" — it does; the bug is downstream of disambiguation.

- **"confidence" was meaningless for citations.** It was `difflib(full citation, bare title)`,
  structurally ~0 because the citation carries authors/journal/year (a *correct* match scored
  0.014). And `_norm`'s regex `[a-z0-9]+` dropped Cyrillic → Russian titles always scored 0.

- **sci-hub.se is dead here** (DNS NXDOMAIN); `sci-hub.ru` and `sci-hub.st` resolve. The mirror
  was hard-coded.

- **The medenv site-packages copy of `server.py` was edited in place** (its `*.dist-info/RECORD`
  sha256 no longer matched; the other 28 connector files were pristine). That is how
  `_is_likely_paywalled` / `_is_valid_pdf` got there. We did **not** try to re-sync site-packages
  with the vendored copy.

## 3. Round 1 — the wrong-paper fix (+ scoring + sci-hub)

Goal made verifiable: stop saving wrong PDFs as ✓; make the tool fail honestly instead.

1. **DOI-gate** in vendored `server.py::_try_repository_fallback`. New helpers `_normalize_doi`,
   `_title_similar`, `_candidate_matches`: only download a candidate whose normalized DOI equals
   the requested DOI (or, if the candidate has no DOI, whose title clears a 0.75 similarity gate).
   This is the load-bearing fix — it prevented every observed wrong download for free, before any
   bytes are fetched.
2. **Content verification** — new `grab/verify.py`. After a title download, confirm the saved PDF
   is the right paper: `%PDF` magic → pypdf page-1 text → title/author token coverage. Three
   outcomes: `verified` (success), `unverified` (kept + flagged; e.g. scanned PDF with no text —
   DOI gate already vouched), `rejected` (file deleted, run fails). A cheap LLM yes/no resolves
   **only the gray score band**, on valid text — **veto-only**: it can confirm/reject a gray-zone
   match but never rescues a non-PDF / empty-text file into success, and never runs on garbage.
3. **"Matched but not OA-downloadable"** — when a confident match exists but no PDF was obtained,
   report that honestly; **never substitute** a downloadable near-miss (DOI-gate guarantees we
   never save a different-DOI paper).
4. **Scoring** in `disambiguate.py`: `normalize` keeps Cyrillic + `html.unescape`; confidence is
   title-token **containment** in the citation (meaningful for citation inputs; lets clear matches
   resolve as `fuzzy` with 0 tokens); LLM prompt prefers `-1` over a topical near-miss.
5. **Sci-Hub**: default mirror `sci-hub.ru`, overridable via `GRAB_SCIHUB_URL`, threaded through
   `pipeline → download_with_fallback(scihub_base_url=...)`.
6. `grab/_vendor/NOTICE.md` updated to list the real divergences from upstream. Offline tests added.

### Round 1 test results (7-paper targeted batch, OA-first, no --scihub)
Lines 1,5,7,19,21,27,37 of `test_papers.txt`. **0 wrong PDFs (was 6/7).**
- Chow/KEYNOTE-012 → ✓ verified, conf 1.0.
- Vermorken EXTREME, Burtness ECOG, Cohen KEYNOTE-040, Machiels LUX-H&N → honest fail with the
  **correct** matched DOI (paywalled / not OA-indexed).
- RUSSCO (cyrillic now scores 0.889), Al-Sarraf 1987 → ambiguous (exit 2), candidates surfaced
  instead of a wrong download.
- Sci-Hub: `--scihub` run hit `sci-hub.ru` and actually fetched a paywalled NEJM paper;
  `GRAB_SCIHUB_URL=...st` override changed behavior.

## 4. Round 2 — `/review` of the logs: remaining issues + class refactor

Analysis of `tests/downloads_with_verify/run_verify.*` found honest-but-recoverable misses and
cosmetics; the user also asked to organize the code into classes and separate the logic.

1. **OA-landing recall miss (the real recall win).** Cohen and Machiels have free PDFs in the
   UCLouvain DIAL institutional repository, but `pdf_url` points at an **HTML landing page**;
   `_download_from_url` only accepted direct PDFs and bailed. WebFetch confirmed Cohen's DIAL page
   exposes `…/bitstreams/<uuid>/download` = the real Lancet PDF. Fix in vendored `server.py`:
   `_is_pdf_response` + `_extract_pdf_link` (tries `citation_pdf_url` meta, then a DSpace
   bitstream/`download` or `.pdf` href) and **one** retry inside `_download_from_url`. DOI-gate +
   content-verify backstop a wrong grab. Also helps "unpaywall: resolved OA URL but download failed".
2. **Junk candidates.** For RUSSCO/Al-Sarraf the surfaced candidate list included irrelevant arXiv
   physics papers (scores 0.0–0.125; a `>0` filter was insufficient). Fix: `TitleMatcher.CANDIDATE_FLOOR
   = 0.20` + `visible_candidates()` (drops near-zero noise, always shows ≥1).
3. **HTML entities.** `LUX-Head &amp; Neck 1` leaked into the matched title. Fix: `html.unescape`
   in `normalize`, in `server._title_similar`, and when storing titles for display.
4. **Class refactor (requested).** Logic split into classes; the vendored `server.py` stays
   **functional** (do not re-architect upstream — eases re-vendoring):
   - `grab/llm.py` (new) — `LLMClient`: provider/proxy/model plumbing; `has_key/complete/pick/verify_match`.
   - `grab/disambiguate.py` — `TitleMatcher` (+ module-level `normalize`); thresholds are class attrs.
   - `grab/verify.py` — `PdfVerifier` (+ `VerifyResult`).
   - `grab/pipeline.py` — `Grabber` orchestrator; module-level `grab()` kept as a thin wrapper so
     `cli.py` is untouched.

### Round 2 test results
- Offline tests **5/5**: `test_classify`, `test_disambiguate`, `test_verify`, `test_landing`, `test_doi_gate`.
- **Cohen DOI now downloads** (618 KB via unpaywall → DIAL landing → bitstream); page-1 text confirms
  the real KEYNOTE-040 (Cohen E.E.W. et al., Lancet 2019). Previously honest-fail.
- RUSSCO / Al-Sarraf: candidate lists **no longer contain arXiv physics** (only relevant crossref records).
- Machiels matched-title now shows `LUX-Head & Neck 1` (entity decoded).
- Chow → still ✓ verified, conf 1.0 (regression control).
- **Machiels still honest-failed that run** — connector recall variance: CORE/EuropePMC returned
  candidates but none reached download (no `pdf_url` or rejected by the DOI-gate as wrong-DOI), and
  the DIAL record wasn't surfaced. **Not a defect** of the landing fix (proven end-to-end on Cohen);
  honest-fail is the correct outcome over grabbing the wrong "Rationale and design" protocol paper.

## 4b. Round 3 — stopword scoring (A) + identifier-flow content verification (B)

A fresh 7-paper batch (2026-05-30, after a long gap) re-confirmed the guarantees (0 wrong
PDFs as ✓; offline 5/5) and surfaced two improvables. Both fixed; recall issues left alone.

1. **A — stopwords inflated off-topic containment (`disambiguate.py`).** For Burtness/
   KEYNOTE-048 the surfaced list still showed arXiv physics ("ATLAS Experiment" 0.30,
   "CMS/LHCb B⁰ₛ→μμ" 0.25) — they cleared `CANDIDATE_FLOOR=0.20` and entered the LLM's
   8-candidate slice. Diagnosed empirically: those scores came **only** from `the/of/and`
   + the single-char `b` overlap. Fix: module-level `STOPWORDS` (small English function-word
   set) + `content_tokens()` (drops stopwords and `len<2` tokens); applied in `containment()`
   and `rank()`. Cyrillic untouched (RUSSCO was *recall*, not stopword noise). Result: both
   arXiv titles → 0.000, gone from the list and the LLM slice; legit KEYNOTE-048 record stays
   0.688. Side effect: Chow now resolves `fuzzy` conf 1.0 (no LLM call) — wider lead over
   runners-up clears the confident threshold for free.
2. **B — DOI/PMID flow now content-verified (closes the §6 gap).** Title flow always ran
   `PdfVerifier`; identifier flows did not (trusted DOI-gate + exact-id only — metadata trust,
   not file-content trust). Fix in `pipeline.py`: after a successful identifier download,
   `_canonical_meta(kind, value)` fetches the canonical title/authors (reusing
   `server.get_crossref_paper_by_doi` for DOI, `pubmed_searcher.search` for PMID) and runs the
   **same** `_verify_downloaded`. **Soft mode** (user-chosen): only an explicit `rejected`
   deletes; `unverified` is kept+flagged, symmetric with title flow. **Empty-title guard**
   (load-bearing): if no title is obtained (e.g. DOI not in crossref, resolved via Unpaywall),
   verification is **skipped** — never reject against an empty title (would spuriously delete a
   good PDF; `score("",…)=0` ⇒ false reject). arXiv id-flow left unverified (no cheap
   title-by-id reuse hook). Vendored code **untouched** (B reuses existing server functions).
3. **C — field recon (diagnostic, no shipped code).** Question carried over from the user:
   would enriching the LLM prompt help? Dumped the exact dicts handed to `LLMClient.pick()`.
   **Finding: crossref DOES populate `authors` and `published_date`.** RUSSCO candidates all
   show identical authors (Болотина и др.) and *distinct* years (2020/2021/2022) — the LLM
   already sees the year and correctly returns `-1` because the requested **2023** edition is
   simply **not in the index** (recall, not a prompt deficiency). So prompt enrichment would
   NOT fix RUSSCO/Burtness; deferred. (Tangential: crossref's per-year score was 0.875 and the
   displayed year occasionally mismatches the DOI's year — cosmetic, not load-bearing.)

### Round 3 test results
- Offline **7/7**: the 5 modules + new `test_stopwords_dont_inflate_offtopic` (A) and
  `test_doi_flow_skips_verify_without_title` (B empty-title guard).
- **A live:** Burtness candidate list = **only the 3 crossref records, zero arXiv** (was 5 incl.
  ATLAS/LHCb); still correctly ambiguous (real Lancet DOI absent from results — recall).
- **B live:** Cohen `10.1016/s0140-6736(18)31999-8` and Ye `10.21037/atm.2019.01.46` now carry
  `verify: verified 1.0` in the manifest — previously **no** verify block at all.
- **Regression:** Chow → ✓ verified 1.0 (this run as `fuzzy`; first attempt honest-failed at
  *download* then recovered on retry — connector variance per §6, unrelated to A/B).

## 4c. Round 4 — multi-OA-location download (proxy-fallback) + `--batch` CLI

A full 19-citation batch into `tests/downloads_final/{oa,scihub}` (OA 3/19, Sci-Hub 13/19, all
successes verified, 0 wrong PDFs) drove three user questions. Diagnosis was all empirical.

1. **"resolved OA URL but download failed" (6 cases) — real, partly fixable.** Unpaywall often
   lists **multiple** `oa_locations`, but the resolver returned only the **first** (publisher)
   and `_download_from_url` tried it once. NEJM/JCO publisher links are `bronze` and 403 even
   with a browser UA. **Fix R4.1a:** new `UnpaywallResolver.resolve_oa_pdf_urls(doi)` returns
   *all* OA URLs, **repository host_type first**, de-duped (`resolve_best_pdf_url` untouched —
   still used by `get_paper_by_doi`); `download_with_fallback` loops over them.
2. **The deeper cause was the PROXY, not the loop.** Ferris (`10.1056/nejmoa1602252`) *has* a
   repository copy (`air.unimi.it` → bitstream `nihms893611.pdf`, real %PDF) — but that host
   returns **200 via the proxy and 403 direct**, and `cli.py` strips all proxy vars for academic
   traffic. Measured: APIs (crossref/unpaywall/arxiv/europepmc) work **both** ways; only the
   institutional repo needs the proxy. **Fix R4.1b (per user policy "direct first; proxy only
   for what fails; keep it minimal"):** `_download_from_url` split into `_download_from_url_once
   (url, path, proxy=None)` + a wrapper that tries **direct first, then retries via
   `GRAB_DOWNLOAD_PROXY`** only on failure. `cli.py` now sets `GRAB_DOWNLOAD_PROXY` (and fixes
   the latent `HTTP_PROXY`-only capture gap) alongside `GRAB_LLM_PROXY`, then still strips the
   standard proxy vars so APIs stay direct. Proxy scope stays limited to the final file fetch.
3. **`--batch FILE` (R4.2).** `cli.py`: `query` is now optional; `--batch` reads citations
   separated by blank lines (`\n\n`, like `test_papers.txt`, multi-line citations joined), runs
   each via the existing `grab()`, and writes a human-readable `run.log` next to the existing
   `manifest.jsonl` in `--out`, ending with a `downloaded N/M · ambiguous K · failed F` summary.
   Single-query mode is unchanged (same stdout, exit `0/1/2`); `_format_result` is shared.

Ambiguous-gate (#2 of the user's questions) deliberately **left strict** — the 3 cases are a
nonexistent edition (RUSSCO 2023), a different paper (Al-Sarraf), and an abstract-not-article
(KEYNOTE-048); auto-picking any would download the wrong thing.

### Round 4 test results
- Offline **8/8 modules-worth**: 6 modules incl. new `test_oa_locations` (repository-first
  ordering + dedup via a stubbed payload; blank-line batch parsing).
- **R4.1 live:** Ferris `10.1056/nejmoa1602252` now ✓ **640 KB verified** (direct 403 → proxy
  rescued the `air.unimi.it` repo copy); Cohen still ✓ direct (no regression); NEJM-Verm2008
  `10.1056/nejmoa0802656` (publisher-only, 403 even via proxy) still honest-fails — no wrong grab.
- **R4.2 live:** `grab --batch tests/test_papers.txt --out …/oa2` reproduces all outcomes into a
  tool-written `run.log` + `manifest.jsonl`, no hand-rolled shell driver.

## 5. Design principles (do not regress these)

- **Honest failure > wrong PDF.** Lower apparent recall is fine; the recall removed was fake.
- **Never substitute a different-DOI paper** to force a success (DOI-gate guarantees this).
- **LLM is veto-only and single-shot**, never an agent, never asked to judge empty/garbage input,
  never used to rescue a broken/wrong download into success. Optional: no key ⇒ fully free.
- **Don't touch hermes's `_is_likely_paywalled` / `_is_valid_pdf`** — orthogonal and helpful.
- **Vendored code stays close to upstream**, functional, every edit recorded in `NOTICE.md`.
- **Sci-Hub off by default** (`--scihub`), matching the `paper-download` skill.

## 6. Known limitations / next ideas

- **Connector recall variance**: CORE/EuropePMC/semantic results fluctuate (rate limits, 429s), so a
  paper may download one run and honest-fail the next. Re-running can help; `--scihub` helps for
  papers ≤2021.
- **DOI/PMID flows are now content-verified** (Round 3/B): `_canonical_meta` fetches the title
  and runs `PdfVerifier`, soft-mode, with an empty-title guard. **arXiv id-flow is still not
  content-verified** (no cheap title-by-id reuse hook) — relies on DOI-gate + exact-id fetch.
  Landing-page extraction on identifier flows still trusts the repo record's DOI match.
- **medenv `site-packages/paper_search_mcp/server.py` is patched in-place and out of sync** with the
  vendored copy; we run from the vendored copy via `PYTHONPATH=.`, so this only matters if something
  imports the installed package.
- Landing-page extraction only fires if a searcher actually returns the OA record with a `pdf_url`.

## 7. Quick reference

- Run: `PY=~/myenvs/medenv/bin/python; PYTHONPATH=. $PY -m grab.cli "<query>" --out <dir>`
- Tests: loop `test_classify test_disambiguate test_verify test_landing test_doi_gate` (see CLAUDE.md).
- Editing the repo from a background session needs `.claude/settings.json` ->
  `{"worktree":{"bgIsolation":"none"}}` (we edit in place because the branch carries uncommitted work;
  a worktree from origin/main would lose it). The setting is read at session start.
- Related memories: `grab-oa-fallback-wrong-paper` (now FIXED), `grab-runs-in-medenv`,
  `grab-proxy-only-openai`, `prefers-cheap-deterministic-over-agent`.
