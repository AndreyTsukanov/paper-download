# grab

Download one specific academic paper from the command line — by **DOI, arXiv ID, PMID,
or title**. Open-access first; shadow sources (Sci-Hub, SciDB, Nexus) off by default.

It reuses [`paper-search-mcp`](https://pypi.org/project/paper-search-mcp/)'s connectors
directly as a Python library — **vendored into `grab/_vendor/`** so nothing extra needs
to be installed — with no agent and no tokens for identifier lookups. A single cheap LLM
call is made only to disambiguate an ambiguous title; the provider is configurable
(**OpenAI `gpt-4.1-mini`** by default, or **Anthropic Haiku**).

## Install

```bash
python -m pip install -e .            # installs grab + its dependencies
```

This pulls the vendored connectors' dependencies (`requests`, `httpx`, `beautifulsoup4`,
`lxml`, `pypdf`, `feedparser`, `mcp`, `fastmcp`) plus `anthropic`, `openai`, and
`python-dotenv`.

If you only want the dependencies (not `grab` itself as a package), install them from
`requirements.txt` and run from source with `PYTHONPATH=.`:

```bash
python -m pip install -r requirements.txt
PYTHONPATH=. python -m grab.cli "1706.03762"
```

`requirements.txt` lists the same runtime dependencies as `pyproject.toml`, but
**pinned to exact versions** (a known-working set) for a reproducible install — use it
when you want the environment frozen rather than resolved against the latest releases.

Configuration (provider, keys, model, optional proxy) is read from a `.env` in the project
root. Copy the template to start:

```bash
cp .env.example .env   # then fill in what you need — everything is optional
```

**Do not commit `.env`** — it holds your API key and any proxy credentials (it's in
`.gitignore`). Every value is optional: with no `.env` at all, `grab` still runs fully free.

## Usage

```bash
grab "1706.03762"                       # arXiv ID
grab "10.1038/nature14539"              # DOI
grab "26017442"                         # PMID
grab "Attention is all you need"        # title
grab "10.1234/paywalled" --scihub       # allow the Sci-Hub fallback (only)
grab "10.1234/paywalled" --shadow       # allow ALL shadow fallbacks: Sci-Hub, SciDB, Nexus
grab "..." --out ~/papers               # choose output dir (default ~/Downloads/papers)
grab --batch papers.txt --out ~/papers  # download many (citations separated by blank lines)
```

On success it prints the saved path and size, and appends a record to
`<out>/manifest.jsonl`.

> **For paywalled papers, add `--scihub` or `--shadow`.** Open-access alone covers only a
> fraction — in our 19-paper test batch OA found ~4/19, while OA + Sci-Hub found ~14/19.
> `--scihub` enables Sci-Hub only; **`--shadow`** also tries **SciDB** (Anna's Archive) and
> **Nexus/STC**, which can carry **post-2021** papers Sci-Hub's frozen corpus lacks. The legal
> **Fatcat / Internet Archive Scholar** fallback (preserved archive.org copies, best for old
> paywalled journals) is always on — no flag. Without any shadow flag, paywalled papers honestly
> fail (`matched … but not OA-downloadable`) rather than saving a wrong PDF; every download —
> shadow included — is content-verified, so a wrong/corrupt file is discarded, never saved as ✓.
>
> **Shadow sources are off by default and serve copyrighted paywalled content; enabling them is
> your choice and responsibility.** See [Shadow sources](#shadow-sources-paywalled-fallback) below.

### Batch mode

`--batch FILE` downloads many papers in one run. The file holds one citation per
block, blocks separated by **blank lines** (a citation may span lines) — the same
format as `tests/test_papers.txt`. Each paper goes through the normal OA-first
pipeline; grab writes a human-readable `run.log` (per-citation ✓/?/✗ + a final
`downloaded N/M` summary), a `full_run.log` (the complete terminal output, incl. the
connectors' library logs), and the machine-readable `manifest.jsonl` — all in `--out`.
Combine with `--scihub` or `--shadow` to allow the shadow fallbacks for the whole batch.

Every result carries a **`chain:`** line (and a `chain` field in the manifest) showing what each
source did, e.g. `oa-chain:skipped(NEJM) · fatcat:down · scihub:ok` or
`unpaywall:none · openalex:none · crossref:none · fatcat:down · scihub:not found · scidb:unreachable ·
nexus:skipped (no GRAB_NEXUS_GATEWAY)` — so you can see exactly which legal/shadow source produced (or
failed to produce) the PDF. (`fatcat:down` = the Fatcat host wasn't reachable; for known paywalled
publishers the legal OA chain is skipped, shown as `oa-chain:skipped(...)`, so OpenAlex/Unpaywall only
appear in the chain when that chain actually runs — i.e. OA-only or non-paywalled-publisher flows.)

### Shadow sources (paywalled fallback)

Off by default. Two opt-in flags:

- **`--scihub`** — Sci-Hub only (mirror `GRAB_SCIHUB_URL`, default `sci-hub.ru`).
- **`--shadow`** — all shadow sources, tried in order **Sci-Hub → SciDB → Nexus**.

`--shadow` adds two sources beyond Sci-Hub (whose corpus is frozen at ~2021):

- **SciDB** (Anna's Archive; `GRAB_SCIDB_URL`, default `annas-archive.se`) — the full Sci-Hub
  corpus *plus* newer papers. The mirror domain rotates, so set `GRAB_SCIDB_URL` to a reachable
  one if the default is blocked on your network.
- **Nexus / STC** — IPFS-based and **experimental**: a no-op unless you point `GRAB_NEXUS_GATEWAY`
  at a resolver template (use `{doi}` as the placeholder, e.g. `https://<gateway>/ipns/<key>/{doi}`).

The legal **Fatcat / IA Scholar** fallback (preserved archive.org copies) runs in the OA chain
**always**, with no flag. Every shadow PDF is content-verified before it counts as a success, and
the manifest records a **`via`** field naming the source that produced each file
(`unpaywall` / `openalex/crossref` / `fatcat` / `scihub` / `scidb` / `nexus` / …). These shadow
sources serve copyrighted paywalled content; use is your responsibility.

### Title disambiguation

- A confident string match downloads automatically.
- If the match is ambiguous and the provider's key is set (`OPENAI_API_KEY`, or
  `ANTHROPIC_API_KEY` when `GRAB_LLM_PROVIDER=anthropic`), the LLM picks the right one.
- If it's ambiguous and no key is set, `grab` **lists the candidates and stops** rather
  than guessing — pass a DOI to pick one. (Exit code `2`.)

## Environment

| Variable | Purpose |
|---|---|
| `GRAB_LLM_PROVIDER` | Optional. `openai` (default) or `anthropic`. Selects the disambiguation LLM. |
| `OPENAI_API_KEY` | Optional. Enables LLM tie-breaking when provider is `openai`. |
| `OPENAI_BASE_URL` | Optional. Override the OpenAI endpoint (default `https://api.openai.com/v1`). |
| `ANTHROPIC_API_KEY` | Optional. Enables LLM tie-breaking when `GRAB_LLM_PROVIDER=anthropic`. |
| `GRAB_MODEL` | Optional. Override the disambiguation model (default `gpt-4.1-mini`). |
| `HTTPS_PROXY` / `ALL_PROXY` | Optional. If set (e.g. in `.env`), used **only** by the OpenAI client; all other (academic) requests are forced proxy-less. |
| `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` | Optional. A contact email (not a key) for the Unpaywall OA step. Defaults to a placeholder so it works out of the box; set your own in `.env`. |
| `GRAB_SCIHUB_URL` | Optional. Sci-Hub mirror for `--scihub`/`--shadow` (default `https://sci-hub.ru`). |
| `GRAB_SCIDB_URL` | Optional. SciDB (Anna's Archive) mirror for `--shadow` (default `https://annas-archive.se`; the domain rotates). |
| `GRAB_NEXUS_GATEWAY` | Optional. Nexus/STC resolver URL template for `--shadow` (use `{doi}`); unset ⇒ Nexus is skipped. |

The connectors' library logs (`429`/`No CORE key`/timeout chatter, HTTP traces) are shown in the
terminal; the clean `run.log` never carries them, and batch mode also captures everything in
`full_run.log`.

### Tuning (timeouts / retries)

Network budgets are centralized in `grab/_vendor/paper_search_mcp/config.py` and overridable via env
(fast defaults shown). Semantic Scholar is bounded because, unauthenticated, its `429` storms otherwise
dominate citation-batch time:

| Variable (default) | Effect |
|---|---|
| `GRAB_SEMANTIC_TIMEOUT` (10) · `GRAB_SEMANTIC_MAX_RETRIES` (1) · `GRAB_SEMANTIC_RETRY_DELAY` (2) | Semantic Scholar request timeout / attempts / backoff. With a key you can raise these. |
| `GRAB_FATCAT_CONNECT_TIMEOUT` (4) · `GRAB_FATCAT_READ_TIMEOUT` (6) · `GRAB_FATCAT_FAILURE_LIMIT` (2) | Fatcat timeouts; after N consecutive connection failures it's disabled for the run. |
| `GRAB_SCIDB_TIMEOUT` (8) · `GRAB_NEXUS_TIMEOUT` (10) | Per-request timeout for the SciDB / Nexus shadow fetchers. |

Tip: on a citation batch, `--sources arxiv,crossref` (dropping `semantic`) is the fastest if you have no
Semantic Scholar key.

### Optional API keys

`grab` works with **no keys** — these only raise rate limits and add a few open-access
sources. The warnings you see at startup (`No CORE API key`, `No SEMANTIC_SCHOLAR_API_KEY`,
etc.) are harmless: the connector still runs, just unauthenticated. **All of these keys are
free** — none need to be bought. Set them in `.env` if you want fewer rate limits.

| Key | Where to get it (free) | Effect |
|---|---|---|
| `SEMANTIC_SCHOLAR_API_KEY` | [semanticscholar.org/product/api](https://www.semanticscholar.org/product/api) | In the **default** title-search path. Without it you hit HTTP 429 and Semantic Scholar drops out of the search (arXiv + CrossRef still answer). Approval is manual and can take a while. |
| `CORE_API_KEY` | [core.ac.uk/services/api](https://core.ac.uk/services/api) | Used by the OA-download fallback chain. |
| `OPENAIRE_API_KEY` | [graph.openaire.eu](https://graph.openaire.eu/develop/overview.html) | Used by the OA-download fallback chain. |
| `DOAJ_API_KEY` | [doaj.org/api](https://doaj.org/api) | Only used if you add `doaj` via `--sources`; instant and free. |

`PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` is **not** a key — just a contact email Unpaywall asks
for. It defaults to a placeholder so the OA step works out of the box; set your own
address via `.env` (Unpaywall asks callers to identify themselves).

Exit codes: `0` success · `1` failed · `2` ambiguous title.
