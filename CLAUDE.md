# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`grab` — a command-line tool that downloads **one specific** academic paper given a
DOI, arXiv ID, PMID, or title. Open-access first. It is the scripted, token-cheap
counterpart to the `paper-download` Claude skill (`~/.claude/skills/paper-download/`):
same OA-first philosophy, but deterministic and runnable without an agent.

> **Change history & rationale** (why the DOI-gate, content verification, landing-page
> extraction, and the class split exist — read this before changing the download/verify logic):
> @CHANGES_MEMORY.md

## Core architectural decision

The heavy lifting is **not** done by an LLM agent. `paper_search_mcp` is **vendored**
into `grab/_vendor/paper_search_mcp/` (MIT, v0.1.4 — see `grab/_vendor/NOTICE.md`), and its
`@mcp.tool()`-decorated functions are left callable — so we
`await grab._vendor.paper_search_mcp.server.download_with_fallback(...)` and
`search_papers(...)` **directly**. No MCP subprocess, no agent loop, no tool schemas in
a prompt. That entire download chain (source-native → OA repos → Unpaywall → optional
Sci-Hub) is reused; **never reimplement it here**.

The vendored copy is **almost** upstream (the `@mcp.tool()` decorators are no-ops for our
use), with a few documented edits in `server.py` — see `grab/_vendor/NOTICE.md`. The
load-bearing one is a **DOI-match gate** in `_try_repository_fallback`: upstream downloads
the first OA result with any `pdf_url`, which for non-OA papers silently saves a
topically-similar but *wrong* PDF; the gate only accepts a candidate whose DOI (or, lacking
one, title) matches the request. A second edit lets `_download_from_url` follow an OA
*landing page* (HTML) to the real PDF link (`citation_pdf_url` / DSpace bitstream / `.pdf`),
which upstream would reject outright. The vendored package is kept **functional** (not
re-architected into classes) to ease re-vendoring. If you fix a connector, edit it there and
record it in `grab/_vendor/NOTICE.md`. It still pulls its own third-party libs (`requests`,
`httpx`, `bs4`, `lxml`, `pypdf`, `feedparser`, `mcp`, `fastmcp`), declared in `pyproject.toml`.

An LLM is used in at most two cheap, single-shot spots (never an agent): (1) disambiguating
a fuzzy title when string matching is not confident, and (2) a yes/no check that a *downloaded*
PDF really is the requested paper — but only for the deterministic "gray zone" (see Pipeline).
Both are optional: without a key the tool is fully free. The provider is configurable via
`GRAB_LLM_PROVIDER`: **OpenAI** (`gpt-4.1-mini`) by default, or **Anthropic** (Haiku). Config
(keys, model, optional proxy) is read from a project-root `.env` via python-dotenv. All LLM
plumbing lives in one place: `grab/llm.py` (`LLMClient`).

## Pipeline (grab/pipeline.py)

```
input ─▶ classify (regex, free)
          ├─ doi / arxiv / pmid ─▶ download_with_fallback()              [0 tokens]
          └─ title ─▶ search_papers() ─▶ containment score
                        ├─ confident match          ─▶ download
                        ├─ ambiguous + LLM key set   ─▶ LLM picks ─▶ download
                        └─ ambiguous, no key        ─▶ surface candidates, DO NOT download

OA repository fallback is DOI-gated (only a DOI/title match is ever downloaded); an OA
landing page is followed to its real PDF link before giving up. Unpaywall returns *all*
its OA locations (repository host_type first); each PDF fetch tries a direct connection
first and only falls back to the proxy (`GRAB_DOWNLOAD_PROXY`) when direct fails.
After any title download ─▶ verify (grab/verify.py): verified | unverified | rejected→discard.
DOI/PMID downloads are verified too: the canonical title is fetched (crossref-by-DOI /
PubMed) and run through the same verifier, but only when a title is obtained (empty-title
guard — never reject against an empty expected title). arXiv id-flow stays unverified.
```

Two load-bearing guards:

- **Disambiguation** never downloads a near-miss: silently grabbing "Is Attention All You
  Need?" for "Attention Is All You Need" is the failure mode this tool exists to prevent. Only
  `method in {"fuzzy", "llm"}` auto-downloads. Title score is token *containment* (how much of
  the candidate title appears in the citation), which stays meaningful for citation-style
  inputs where plain difflib was ~0. `TitleMatcher.visible_candidates()` hides near-zero search
  noise (e.g. off-topic arXiv hits) from the surfaced candidate list.
- **Content verification** never trusts a green ✓ blindly: after a title download, `PdfVerifier`
  confirms the saved PDF is the right paper (`%PDF` magic → pypdf page-1 text → title/author
  coverage; the gray zone falls to one cheap LLM yes/no, veto-only, never on empty/garbage
  input). A `rejected` file is deleted and the run fails honestly; a confident-but-undownloadable
  match reports "matched … but not OA-downloadable" rather than substituting a near-miss.

## Files

- `grab/classify.py` — pure regex routing → `("doi"|"arxiv"|"pmid"|"title", value)`. A bare arXiv id always has a dot; a PMID never does — that's the disambiguator.
- `grab/llm.py` — `LLMClient`: the only place the cheap LLM lives (provider/model/proxy). `has_key()`, `complete()`, `pick()` (disambiguation; prompt prefers `-1` over a topical near-miss), `verify_match()` (gray-zone yes/no). OpenAI routes through `GRAB_LLM_PROXY` if present; no key ⇒ `has_key()` is False and the tool runs free.
- `grab/disambiguate.py` — module-level `normalize()` (lowercased ascii+**cyrillic** tokens, HTML entities decoded; stdlib only — NOT rapidfuzz, which isn't in `medenv`), `STOPWORDS` + `content_tokens()` (drop English function words and single-char tokens before scoring, so off-topic titles can't score on `the/of/and`), and `class TitleMatcher`: scores candidates by title-token **containment** in the citation; `rank()`, `choose()` → `(pick, confidence, method, ranked)`, `visible_candidates()` (drops near-zero noise via `CANDIDATE_FLOOR`). Not-confident + key set ⇒ one `LLMClient.pick()`; any LLM error degrades to surface-candidates (`lowconf`), never a crash. Thresholds are class attributes.
- `grab/verify.py` — `class PdfVerifier` (+ `VerifyResult(status, score, reason)`). Deterministic first (`%PDF` magic, pypdf page-1 text, title/author coverage); a cheap `LLMClient.verify_match()` resolves only the gray zone, on valid text. LLM is veto-only: it can confirm or reject a gray-zone match but never rescues a non-PDF / empty-text file into success.
- `grab/pipeline.py` — `class Grabber` orchestrates classify → match → download → verify → log (reuses `paper_search_mcp.server` directly; content-verifies each title download and discards rejects; for DOI/PMID downloads `_canonical_meta()` fetches the title and runs the same verifier under an empty-title guard; threads the Sci-Hub mirror via `scihub_base_url`; appends a `manifest.jsonl` to the output dir on every run). Module-level `grab()` is a thin wrapper kept for the CLI.
- `grab/cli.py` — argparse front end; loads `.env` (python-dotenv); sets the Unpaywall email default for the OA chain; captures the proxy into `GRAB_LLM_PROXY` (OpenAI) and `GRAB_DOWNLOAD_PROXY` (PDF-download fallback), then strips all standard proxy vars so the academic search/API connectors run proxy-less (see Gotchas for the ordering subtlety). Also provides `--batch FILE` (citations split on blank lines) which writes a human-readable `run.log` beside `manifest.jsonl`; single-query mode is unchanged (`_format_result` is shared).
- `grab/_vendor/paper_search_mcp/` — vendored upstream package, kept **functional** (not re-architected). Do not reimplement; edit only to fix a connector or harden a correctness gate (the DOI-gate and landing-page PDF extraction), and record it in `NOTICE.md`.

## Running & developing

The project declares all its dependencies in `pyproject.toml`, so `pip install -e .`
makes it run in any venv. In practice use `medenv` — it already has every transitive lib
installed, so no install step is needed to run from source. `paper_search_mcp` no longer
needs to be pip-installed (it's vendored).

```bash
PY=~/myenvs/medenv/bin/python

# run without installing
PYTHONPATH=. $PY -m grab.cli "1706.03762" --out ~/Downloads/papers
PYTHONPATH=. $PY -m grab.cli "10.1038/nature14539"
PYTHONPATH=. $PY -m grab.cli "Attention is all you need"   # ambiguous → lists candidates

# or install the console script into medenv
$PY -m pip install -e .
grab "1706.03762"

# tests (pytest is not in medenv; run each module directly)
for m in tests.test_classify tests.test_disambiguate tests.test_verify tests.test_landing tests.test_doi_gate tests.test_oa_locations tests.test_shadow_sources; do
  PYTHONPATH=. $PY -c "import $m as t; [getattr(t,n)() for n in dir(t) if n.startswith('test_')]; print('$m ok')"
done
```

Exit codes: `0` success · `1` download failed (or content verification rejected the file) · `2` ambiguous title (candidates printed).

## Gotchas

- **Title-search recall depends on upstream connectors.** In some networks the arxiv /
  semantic *search* APIs hang or return 0 with no error (PDF *download* from arxiv.org
  still works). When that happens only crossref answers, recall drops, and titles become
  ambiguous — expected, handled by the surface-candidates branch. Identifier lookups are
  unaffected. Results also fluctuate between runs (rate limits / 429s): a paper may download
  one run and honest-fail the next.
- **Honest failure is by design.** With the DOI-gate, papers that aren't actually available
  in OA now fail cleanly (`matched … but not OA-downloadable`) instead of saving an unrelated
  PDF as ✓. Lower apparent recall, but the recall it removed was fake.
- **OA landing pages are followed.** When a resolved OA URL returns HTML (institutional repos
  like DSpace/DIAL), `_download_from_url` parses it for the real PDF link (`citation_pdf_url`,
  bitstream/`download`, or `.pdf`) and fetches that once — recovering OA copies upstream dropped.
- The disambiguation LLM is **provider-configurable** via `GRAB_LLM_PROVIDER` (default
  `openai`). The provider's key (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) is optional —
  without it the tool is fully free, ambiguous titles are surfaced rather than auto-resolved,
  and the verification gray zone is left `unverified` rather than LLM-checked. Model is
  overridable via `GRAB_MODEL` (default `gpt-4.1-mini`; for the Anthropic provider set e.g.
  `claude-haiku-4-5-20251001`). All of this is read from `.env`.
- **Proxy handling.** Policy: *direct first; the proxy is used only for what fails direct, and
  kept minimal.* `cli.py` captures the `.env` proxy (`HTTPS_`/`HTTP_`/`ALL_PROXY`) into two
  scoped vars — `GRAB_LLM_PROXY` (the OpenAI httpx client, `trust_env=False`) and
  `GRAB_DOWNLOAD_PROXY` (PDF-download **fallback**: `_download_from_url` tries direct, then the
  proxy only if that fails) — then strips every standard proxy var so the academic **search/API**
  connectors run proxy-less (they work direct; measured). Rationale: some institutional repos
  (e.g. `air.unimi.it`) are reachable *only* via the proxy, while APIs work either way. Ordering
  matters: the vendored `paper_search_mcp/config.py` re-injects `.env` values (including the
  proxy) via `os.environ.setdefault` at import, so the strip in `cli.py` runs *after* that
  import (and forces the vendored load) to actually stick.
- **`.env` holds secrets** (API key, proxy credentials). It must **not** be committed — add it
  to `.gitignore`. If a key leaks into history, rotating it is the only real fix.
- **Shadow sources are OFF by default**, matching the `paper-download` skill. Two opt-in flags:
  `--scihub` (Sci-Hub only) and `--shadow` (ALL shadow sources, tried in order **scihub → scidb →
  nexus**). Mirrors/endpoints: `GRAB_SCIHUB_URL` (default `sci-hub.ru`; the old `sci-hub.se` no
  longer resolves), `GRAB_SCIDB_URL` (default `annas-archive.se`; domain rotates), `GRAB_NEXUS_GATEWAY`
  (no default — Nexus/STC is experimental and a no-op until set). Threading is a `shadow_sources:
  list[str]` (cli → `Grabber` → `download_with_fallback._try_shadow_sources`); the legacy
  `use_scihub=True` still maps to `["scihub"]`. **Fatcat/IA Scholar** (`fatcat.py`, legal preserved
  archive.org copies) runs in the OA chain **always** (no flag). Every shadow PDF is still
  content-verified before it counts as success; the manifest records a `via` field (which source won).
- **Download chain is visible.** `download_with_fallback(trace=…)` records a
  `source:outcome` token per stage (`unpaywall:none`, `openalex:403`, `fatcat:down`, `scihub:not found`,
  `scidb:unreachable`, `nexus:skipped …`); `pipeline` surfaces it as `result['chain']` — printed as a
  `chain:` line and stored in the manifest (`scidb`/`nexus` set `last_status`, `fatcat` tracks
  `_last_unreachable`, so "down" vs "none" is honest). Batch mode also writes **`full_run.log`** (the full
  terminal) beside `run.log`.
- **Library logs are shown; tunables are centralized.** `cli.py::_configure_logging(True)` (called at
  import) keeps the connectors' library logging visible in the terminal (only `InsecureRequestWarning` is
  silenced); the clean `run.log` never carries it, and batch mode captures everything in `full_run.log`.
  Network budgets live in
  `_vendor/paper_search_mcp/config.py` (env-overridable): `GRAB_SEMANTIC_TIMEOUT/MAX_RETRIES/RETRY_DELAY`
  (Semantic Scholar is bounded — its unauthenticated 429-storms were the dominant citation-batch
  time-sink; it now fails fast), `GRAB_FATCAT_CONNECT_TIMEOUT/READ_TIMEOUT/FAILURE_LIMIT` (short timeouts
  + a circuit breaker that disables a down Fatcat for the run), `GRAB_SCIDB_TIMEOUT`, `GRAB_NEXUS_TIMEOUT`.
- `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` is defaulted in `cli.py` so the OA Unpaywall step
  works for plain script runs (the global `~/.claude/.mcp.json` only sets it for the MCP
  subprocess).
