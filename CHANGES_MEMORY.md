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

## 4d. Round 5 — OpenAlex + Crossref as independent OA-location providers (Track 2)

A fresh BRAINSTORMING session (2026-05-31) re-diagnosed "non-100% recall": **not a bug.** The
ad-hoc arXiv test (`2605.28773`) actually *succeeded* (it's a 2026 OA preprint, pulled straight from
arXiv — misread as a failure). OA-only gave 4/19, `--scihub` 14/19; the 5 misses are 2 ASCO
*conference abstracts* (no PDF exists anywhere) + 3 index/recall gaps (RUSSCO 2023, KEYNOTE-048,
Al-Sarraf 1987). A 4-track roadmap was recorded in `BRAIN_STORM.md`. Track 1 (free CORE/Semantic
keys) is **shelved**: "free" means no charge but **manual approval**, and the user was *denied* —
which is exactly why Track 2 matters (OpenAlex = instant self-service key / mailto; Crossref polite
pool = just a mailto; neither needs human approval).

**Track 2 (this round):** add OpenAlex + Crossref as independent OA-location providers in the
download chain, looked up by **exact DOI** (so no fuzzy DOI-gate needed; content-verify still
backstops). Vendored edits, logged in `NOTICE.md` (now 8 changes):
- `academic_platforms/openalex.py`: new `OpenAlexSearcher.resolve_oa_pdf_urls(doi)` →
  `works/https://doi.org/<doi>`, returns all `locations[].pdf_url` + `best_oa_location` +
  `open_access.oa_url`, repository-version first, de-duped. Reads `UNPAYWALL_EMAIL` (polite pool) +
  optional `OPENALEX_API_KEY`.
- `server.py::download_with_fallback`: new block after the Unpaywall loop, before Sci-Hub — tries
  the OpenAlex URLs plus Crossref's exact-DOI `link[]` PDF (`crossref_searcher.get_paper_by_doi`).
- `tests/test_oa_locations.py`: added 3 OpenAlex cases (repo-first ordering, DOI-prefix stripping,
  404/empty), stubbed session, no network.

### Round 5 test results
- Offline **6/6** modules (the 5 + `test_oa_locations` with the new OpenAlex cases).
- **Resolver live (no manually-approved key needed):** OpenAlex returns real URLs — Cohen
  `10.1016/s0140-6736(18)31999-8` → the UCLouvain **DIAL handle** (a green copy), peerj → doi.org,
  NEJM/ASCO → publisher links. Confirms it works on the polite-pool mailto alone.
- **Pipeline:** Cohen downloads + verifies OA-only (604 KB) — chain healthy, **0 wrong PDFs**.
- **Honest-fail preserved:** for genuinely-paywalled DOIs (Burtness/Gibson/Forastiere/Grau/Machiels
  `…2015…`) **both** OpenAlex and Crossref return nothing → the block adds nothing and the run
  honest-fails cleanly (no fabricated success). On *this* oncology batch the incremental recall is
  ~0 because the remaining items are genuinely paywalled with no green copy; the gain is on corpora
  where OpenAlex indexes a green copy Unpaywall missed (proven mechanism: Cohen's DIAL handle).
- Tracks 3 (Fatcat/IA Scholar) and 4 (shadow SciDB/Nexus, opt-in) are deferred to later rounds.

## 4e. Round 6 — Fatcat/IA Scholar (Track 3) + shadow beyond Sci-Hub (Track 4) + `--shadow` flag

Continuation of the same BRAINSTORMING thread (2026-06-03). A **test-on-first-3** of
`PAPERS_FROM_NINEL.txt` first confirmed Track 2 live in the real pipeline (Vermorken 2008 emitted
`openalex/crossref: OA links found but download failed` — the new block fires; the 3 are genuinely
paywalled/abstract so OA recall is correctly ~0). Then Tracks 3 & 4 were implemented.

User decisions: one **`--shadow`** boolean enables ALL shadow sources in order **scihub → scidb →
nexus**; **`--scihub`** stays as the Sci-Hub-only alias. Track-4 sources = **SciDB + Nexus** (LibGen
scimag excluded: its corpus is ≤2020 = Sci-Hub's, no gain). Validate on the `PAPERS_FROM_NINEL.txt`
citations Sci-Hub missed.

**Implementation** (all vendored, logged in `NOTICE.md`, now ten divergences):
- **Track 3 — Fatcat (legal, always-on).** New `academic_platforms/fatcat.py`
  (`FatcatResolver.resolve_pdf_urls(doi)` → `/v0/release/lookup?doi=…&expand=files`, preserved
  archive.org PDFs, archive-first, deduped). New `server._try_fatcat` helper, called in the legal chain
  after OpenAlex/Crossref **and** inside the `_is_likely_paywalled` shortcut *before* any shadow source
  (prefer a legal preserved copy). Exact-DOI ⇒ no fuzzy gate; graceful skip on error.
- **Track 4 — shadow connectors (opt-in, off by default).** New `academic_platforms/scidb.py`
  (`SciDBFetcher`: `<GRAB_SCIDB_URL>/scidb/<doi>` → generic PDF-link extraction → `%PDF`-validated,
  direct→`GRAB_DOWNLOAD_PROXY`) and `academic_platforms/nexus.py` (`NexusFetcher`: **experimental**,
  IPFS/STC has no clean REST, so it's a no-op unless `GRAB_NEXUS_GATEWAY` template is set). SciDB/Nexus
  files are name-prefixed (`scidb_`/`nexus_`) so provenance stays distinct from Sci-Hub's bare-hash names.
- **Flag/chain refactor (back-compat).** `download_with_fallback` gained `shadow_sources: List[str]` +
  `_try_shadow_sources` (loops enabled connectors via `_shadow_fetcher`); the old Sci-Hub tail and the
  shortcut both route through it. `use_scihub`/`scihub_base_url` kept; `use_scihub=True` (no list) ⇒
  `["scihub"]` (identical prior behaviour). `cli.py`: `--shadow` (+`--scihub` alias) →
  `_shadow_sources_from_args`. `pipeline.py`: `Grabber`/`grab()` take `shadow_sources` (not `use_scihub`)
  and add a **`via`** field to the manifest (which source produced the PDF). The content-verify safety net
  is unchanged, so a wrong/corrupt shadow PDF is still discarded, never saved as ✓.

### Round 6 test results
- Offline **7/7**: the 6 prior modules + new `test_shadow_sources` (Fatcat archive-first/dedup/pdf-only,
  SciDB PDF-link parse + direct-PDF save, `--shadow` flag→list mapping, `via` inference).
- **Live (reachable parts):** KEYNOTE-048 DOI `--shadow` → ✓ 602 KB, **verify: verified**, `via scihub`
  (shadow loop end-to-end; content-verify confirmed the right paper). Guigay ASCO-abstract DOI `--shadow`
  → honest-fail (shortcut → Fatcat skip → scihub/scidb/nexus all fail; no file). Cohen DOI **no flags** →
  ✓ `via unpaywall` (back-compat / legal chain intact). `via` provenance verified in the manifest.
- **Sandbox limitation (honest):** from this environment **Fatcat, Anna's Archive (SciDB) and Nexus are
  unreachable** (network-egress block: connect-timeout/SSL); Sci-Hub and all OA APIs work. So the
  **unique post-2021 value of SciDB/Nexus (e.g. RUSSCO 2023) was NOT demonstrated here** — they
  graceful-skip without breaking the run. Validate on a network where those hosts resolve:
  `PYTHONPATH=. $PY -m grab.cli "<post-2021 DOI>" --shadow --out <dir>` (set `GRAB_SCIDB_URL` to a live
  mirror if needed). The SciDB PDF-link parser is generic/best-effort and may need selector tuning against
  a real page; Nexus needs `GRAB_NEXUS_GATEWAY`. **0 wrong PDFs** throughout.

## 4f. Round 6b — speed/quiet pass (comparing `--scihub` vs `--shadow` on the same batch)

A real user run compared `--scihub` (downdownsci) vs `--shadow` (downdownshadow) on the 19-citation
set: **identical outcomes (14/19 · 3 ambiguous · 2 failed)** — Track 4 added 0 on this pre-2021,
Sci-Hub-covered corpus (expected; SciDB/Nexus target post-2021, and were network-unreachable). But
`--shadow` ran ~3× slower (≈23 min vs ≈7.5 min) and the terminal was a wall of warnings. Diagnosis:
(a) **Fatcat `ConnectTimeout` 15 s × ~16 paywalled DOIs ≈ 4 min** (shadow-specific); (b) the rest was
**Semantic Scholar** 429-storms + 30 s read-timeouts on the *title-search* path — present in *both*
runs, contributing ~0 results without a key (Crossref/arXiv answer). Fixes (user picked "bound, don't
drop" for S2):
- **Quiet logs by default** (`cli.py::_configure_logging`, called at import so connector import-time
  warnings are caught too): raises vendored connector + httpx/urllib3 logger levels, silences
  `InsecureRequestWarning`; terminal then showed only the `✓/?/✗` lines, `run.log` unchanged.
  *(This quiet-by-default + a `--verbose` toggle were later reverted — see §4h.)*
- **Centralized, env-tunable network budgets** in vendored `config.py` (see NOTICE). **Semantic Scholar
  bounded** (`request_api`: 3→`SEMANTIC_MAX_RETRIES=1`, 5/10s→`RETRY_DELAY=2`, 30→`TIMEOUT=10`) →
  fail-fast instead of stalling. **Fatcat** short timeouts + circuit-breaker (disable after
  `FATCAT_FAILURE_LIMIT=2` connection failures). SciDB/Nexus timeouts 8/10 s.
- **Measured:** 3 paywalled DOIs `--shadow` → 10 s, clean output, all verified, `via scihub`. 2 citations
  `--shadow` → 77 s (was ~3–4 min/2 in the slow run). Offline **7/7**. Env override verified
  (`GRAB_SEMANTIC_TIMEOUT=20` etc.). Left the two PDF-download `timeout=30`s in `semantic.py` (381/426)
  alone — not the slowdown. **0 wrong PDFs**, no recall change.

## 4g. Round 6c — chain visibility (why only Sci-Hub showed) + full_run.log

User questions exposed an opacity bug: in the `--shadow` run the log only ever showed `scihub`, and the
failed abstracts said the opaque `shadow (scihub,scidb,nexus): download failed`. Diagnosis: (a) the
paywalled-publisher shortcut routes most of this batch straight past the legal OA chain to Fatcat→shadow,
and Sci-Hub (first in the shadow order) wins, so `via`≈scihub everywhere; (b) **SciDB** failed silently
(`annas-archive.se` doesn't even resolve — DNS `gaierror`, ~0 s; its failures logged at `debug`) and
**Nexus** is a no-op without `GRAB_NEXUS_GATEWAY`; only **Sci-Hub** logged loudly (`sci_hub.py` uses bare
`logging.error`). And "Fatcat is down" = a **connect timeout** to `api.fatcat.wiki` (DNS resolves to an IA
IP, but :443 doesn't answer while `archive.org`/`scholar.archive.org` connect in 0.2 s → the Fatcat API
service is down, not a local firewall). The shortcut behaviour was left **as-is** (user's call — keep it).

Fixes (visibility, no recall change):
- **Per-source chain trace** (`server.py`): `download_with_fallback(trace=[])` records a `source:outcome`
  token at each stage; `_try_shadow_sources`/`_try_fatcat` fill it; `scidb`/`nexus` expose `last_status`,
  `fatcat` tracks `_last_unreachable` (so "down" vs "none" is honest). `pipeline` surfaces it as
  `result['chain']` → a `chain:` line in stdout/run.log + a `chain` field in the manifest. Example fail
  line: `oa-chain:skipped(ASCO) · fatcat:down · scihub:not found · scidb:unreachable · nexus:skipped (no
  GRAB_NEXUS_GATEWAY)`. OpenAlex/Unpaywall appear in the chain whenever the legal chain actually runs
  (OA-only or non-paywalled-publisher flows).
- **`full_run.log`** (`cli.py::_run_batch`): the complete terminal session (printed blocks + a root
  `StreamHandler` capturing any library logs) written beside the clean `run.log`.
- **Root-logger quieting** (`cli.py::_configure_logging`): set the **root** logger to CRITICAL by default
  (the earlier per-logger approach missed Sci-Hub's bare `logging.error`). The terminal then showed only
  `✓/?/✗` + `chain:`. *(Reverted to verbose-by-default in §4h.)*
- Offline **7/7**; live: Vermorken DOI `--shadow` → ✓ `chain: oa-chain:skipped(NEJM) · fatcat:down ·
  scihub:ok`; Guigay abstract → honest-fail with the full per-source chain; `full_run.log` + manifest
  `chain`/`via` populated. **0 wrong PDFs**, recall unchanged.

## 4h. Round 6d — verbose logs by default (`--verbose` removed)

User preference: drop the `--verbose` flag and make verbose logging the default. `cli.py` now calls
`_configure_logging(True)` at import and the `--verbose` argument is gone — the connectors' library logs
are shown in the terminal again (only `InsecureRequestWarning` stays silenced). `run.log` stays clean and
`full_run.log` still captures the full terminal; the chain trace, the `config.py` tunables, and Sci-Hub/
SciDB/Nexus behaviour are unchanged. (The `_configure_logging` quiet branch remains in the file but is no
longer invoked.) This reverts the quiet-by-default from §4f/§4g — recall and `0 wrong PDFs` unaffected.

## 5. Design principles (do not regress these)

- **Honest failure > wrong PDF.** Lower apparent recall is fine; the recall removed was fake.
- **Never substitute a different-DOI paper** to force a success (DOI-gate guarantees this).
- **LLM is veto-only and single-shot**, never an agent, never asked to judge empty/garbage input,
  never used to rescue a broken/wrong download into success. Optional: no key ⇒ fully free.
- **Don't touch hermes's `_is_likely_paywalled` / `_is_valid_pdf`** — orthogonal and helpful.
- **Vendored code stays close to upstream**, functional, every edit recorded in `NOTICE.md`.
- **Shadow sources off by default** (`--scihub` = Sci-Hub only; `--shadow` = Sci-Hub+SciDB+Nexus),
  matching the `paper-download` skill. Every shadow PDF is content-verified before it counts as success.

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
- Tests: loop `test_classify test_disambiguate test_verify test_landing test_doi_gate test_oa_locations test_shadow_sources` (see CLAUDE.md).
- Editing the repo from a background session needs `.claude/settings.json` ->
  `{"worktree":{"bgIsolation":"none"}}` (we edit in place because the branch carries uncommitted work;
  a worktree from origin/main would lose it). The setting is read at session start.
- Related memories: `grab-oa-fallback-wrong-paper` (now FIXED), `grab-runs-in-medenv`,
  `grab-proxy-only-openai`, `prefers-cheap-deterministic-over-agent`.
