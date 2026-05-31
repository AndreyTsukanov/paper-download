# grab

Download one specific academic paper from the command line — by **DOI, arXiv ID, PMID,
or title**. Open-access first; Sci-Hub off by default.

It reuses [`paper-search-mcp`](https://pypi.org/project/paper-search-mcp/)'s connectors
directly as a Python library — **vendored into `grab/_vendor/`** so nothing extra needs
to be installed — with no agent and no tokens for identifier lookups. A single cheap LLM
call is made only to disambiguate an ambiguous title; the provider is configurable
(**OpenAI `gpt-4.1-mini`** by default, or **Anthropic Haiku**).

## Install

```bash
~/myenvs/medenv/bin/python -m pip install -e .
```

This pulls the vendored connectors' dependencies (`requests`, `httpx`, `beautifulsoup4`,
`lxml`, `pypdf`, `feedparser`, `mcp`, `fastmcp`) plus `anthropic`, `openai`, and
`python-dotenv`. The `medenv` virtualenv already has them, so you can also run straight
from source with `PYTHONPATH=.`.

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
grab "10.1234/paywalled" --scihub       # allow Sci-Hub fallback
grab "..." --out ~/papers               # choose output dir (default ~/Downloads/papers)
grab --batch tests/PAPERS_FROM_NINEL.txt --out ~/papers  # download many (citations separated by blank lines)
```

On success it prints the saved path and size, and appends a record to
`<out>/manifest.jsonl`.

> **For best recall, add `--scihub`.** Open-access alone covers only a fraction of
> paywalled papers — in our 19-paper test batch, OA found 3/19 while OA+`--scihub`
> found 13/19. Without `--scihub`, paywalled papers honestly fail (`matched … but not
> OA-downloadable`) rather than saving a wrong PDF.
>
> **Recent papers (published after ~2021) may not download — even with `--scihub`.**
> Sci-Hub stopped ingesting new content around 2021, so newer paywalled papers are
> often simply not there. Truly open-access papers download regardless of year; it's
> *paywalled* papers from after ~2021 that tend to fail.

### Batch mode

`--batch FILE` downloads many papers in one run. The file holds one citation per
block, blocks separated by **blank lines** (a citation may span lines) — the same
format as `tests/test_papers.txt`. Each paper goes through the normal OA-first
pipeline; grab writes a human-readable `run.log` (per-citation ✓/?/✗ + a final
`downloaded N/M` summary) alongside the machine-readable `manifest.jsonl` in `--out`.
Combine with `--scihub` to allow the Sci-Hub fallback for the whole batch.

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
| `GRAB_SCIHUB_URL` | Optional. Sci-Hub mirror for `--scihub` (default `https://sci-hub.ru`). |

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
