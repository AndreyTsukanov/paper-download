import os
import re
import sys
import logging
import argparse
import asyncio
from pathlib import Path

# Load .env from project root (where pyproject.toml lives)
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

# Capture the proxy before stripping the standard proxy vars below. Two scoped uses:
#   GRAB_LLM_PROXY      -> the OpenAI client (only reachable via the proxy here)
#   GRAB_DOWNLOAD_PROXY -> fallback for PDF downloads from hosts that fail direct
#                          (e.g. some institutional repositories). Direct is tried
#                          first; the proxy is used only when direct fails.
# Academic search/API requests still run proxy-less (they work direct).
_proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
          or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
          or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"))
if _proxy:
    os.environ["GRAB_LLM_PROXY"] = _proxy
    os.environ["GRAB_DOWNLOAD_PROXY"] = _proxy

# Keep the OA Unpaywall fallback working when run as a plain script (the global
# .mcp.json sets this only for the MCP subprocess). This is a contact email, not a
# key; set your own via PAPER_SEARCH_MCP_UNPAYWALL_EMAIL in .env (the placeholder
# below just keeps the OA step working out of the box).
os.environ.setdefault("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "you@example.com")


def _configure_logging(verbose: bool):
    """Quiet the vendored connectors' noisy library logging by default.

    The per-connector WARNING/INFO chatter (No CORE/DOAJ key, Semantic Scholar 429
    retries, Fatcat timeouts, HTTP-request traces, 'skipping OA chain') is diagnostic
    only — the clean run.log never carried it. Called once at import (before the
    connectors load, so their import-time 'No CORE/DOAJ key' warnings are suppressed
    too) and again from main(). `--verbose` restores full INFO logging.
    """
    names = ("grab._vendor.paper_search_mcp", "httpx", "httpcore", "openai", "urllib3")
    if verbose:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger().setLevel(logging.INFO)
        for name in names:
            logging.getLogger(name).setLevel(logging.NOTSET)
        return
    try:  # urllib3 InsecureRequestWarning is a warnings-module warning, not logging
        import warnings
        from urllib3.exceptions import InsecureRequestWarning
        warnings.simplefilter("ignore", InsecureRequestWarning)
    except Exception:
        pass
    # Quiet the *root* logger — that catches both child-logger records and the bare
    # `logging.error(...)` calls some connectors use (e.g. Sci-Hub's "Could not find PDF
    # URL"). The chain trace + run.log carry the meaningful info; --verbose restores all.
    logging.getLogger().setLevel(logging.CRITICAL)
    for name in names:
        logging.getLogger(name).setLevel(logging.CRITICAL)


# Verbose logging by default: show the connectors' library logs.
_configure_logging(True)

from .pipeline import grab, DEFAULT_SOURCES

# The vendored paper_search_mcp.config re-injects .env values (including the proxy)
# via os.environ.setdefault. Force that load, then strip every standard proxy var so
# the academic connectors run proxy-less; the OpenAI client still uses GRAB_LLM_PROXY.
from grab._vendor.paper_search_mcp.config import load_env_file as _load_vendor_env
_load_vendor_env()
for _k in ("http_proxy", "https_proxy", "all_proxy",
           "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)


def _parse_batch_file(path):
    """Citations separated by blank lines (one may span lines), like test_papers.txt."""
    text = Path(path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", text)
    return [" ".join(b.split()) for b in blocks if b.strip()]


SHADOW_ALL = ["scihub", "scidb", "nexus"]


def _shadow_sources_from_args(args):
    """--shadow enables every shadow source (scihub -> scidb -> nexus); --scihub is the
    Sci-Hub-only alias; neither ⇒ legal OA chain only (default)."""
    if getattr(args, "shadow", False):
        return list(SHADOW_ALL)
    if args.scihub:
        return ["scihub"]
    return []


def _format_result(result, query):
    """Lines describing one result (✓/?/✗ + detail), shared by stdout and run.log."""
    lines = []
    if result["success"]:
        lines.append(f"✓ {result['path']} ({result['size'] / 1024:.0f} KB)")
        if result.get("matched"):
            m = result["matched"]
            lines.append(f"  matched: {m['title']}  "
                         f"[{m['source']}, conf={m['confidence']}, {m['method']}]")
        if result.get("verify"):
            v = result["verify"]
            lines.append(f"  verify: {v['status']} ({v['reason']})")
    elif result.get("ambiguous"):
        lines.append(f"? ambiguous: {query}")
        lines.append("  candidates (pass a DOI/arXiv ID to pick one, or set GRAB_LLM_PROVIDER in .env):")
        for c in result["candidates"]:
            lines.append(f"    [{c['score']}] {c['title']}  ({c['doi'] or 'no doi'}) [{c['source']}]")
    else:
        lines.append(f"✗ failed to download: {query}")
        for e in result["errors"]:
            lines.append(f"  - {e}")
    chain = result.get("chain")
    if chain:  # source-by-source outcome of the download chain (which source did what)
        lines.append("  chain: " + " · ".join(chain))
    return lines


def _run_batch(citations, args):
    """Run every citation; write run.log (clean ✓/?/✗) + full_run.log (full terminal,
    incl. any library logs) next to manifest.jsonl."""
    out = os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)
    log_path = os.path.join(out, "run.log")
    full_path = os.path.join(out, "full_run.log")
    n = len(citations)
    counts = {"ok": 0, "ambiguous": 0, "failed": 0}
    shadow = _shadow_sources_from_args(args)

    # full_run.log mirrors the terminal: the printed blocks (below) + whatever library
    # logging is emitted during the run (quiet by default, everything under --verbose).
    full = open(full_path, "w", encoding="utf-8")
    full_handler = logging.StreamHandler(full)
    full_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(full_handler)
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            def emit(text):  # → terminal, the clean run.log, and full_run.log
                print(text)
                log.write(text + "\n"); log.flush()
                full.write(text + "\n"); full.flush()

            emit(f"grab batch: {n} citations -> {out}"
                 + (f" [shadow: {','.join(shadow)}]" if shadow else ""))
            for i, q in enumerate(citations, 1):
                result = asyncio.run(grab(q, args.out, shadow_sources=shadow, sources=args.sources))
                if result["success"]:
                    mark, key = "✓", "ok"
                elif result.get("ambiguous"):
                    mark, key = "?", "ambiguous"
                else:
                    mark, key = "✗", "failed"
                counts[key] += 1
                emit("\n".join([f"[{i}/{n}] {mark} {q[:90]}"]
                               + ["  " + ln for ln in _format_result(result, q)]))
            emit(f"=== done: downloaded {counts['ok']}/{n} · "
                 f"ambiguous {counts['ambiguous']} · failed {counts['failed']} ===")
    finally:
        logging.getLogger().removeHandler(full_handler)
        full.close()
    print(f"(run.log: {log_path} · full_run.log: {full_path} · manifest: {os.path.join(out, 'manifest.jsonl')})")


def main():
    ap = argparse.ArgumentParser(
        prog="grab",
        description="Download a specific academic paper (open-access first).",
    )
    ap.add_argument("query", nargs="?", help="DOI, arXiv ID, PMID, or paper title")
    ap.add_argument("--batch", metavar="FILE",
                    help="file of citations separated by blank lines; downloads each, "
                         "writing run.log + manifest.jsonl to --out")
    ap.add_argument("--out", default="~/Downloads/papers", help="save directory")
    ap.add_argument("--scihub", action="store_true",
                    help="allow the Sci-Hub shadow fallback only (alias for --shadow's first source)")
    ap.add_argument("--shadow", action="store_true",
                    help="allow ALL opt-in shadow fallbacks, in order: sci-hub, SciDB, Nexus "
                         "(off by default; mirrors via GRAB_SCIHUB_URL / GRAB_SCIDB_URL / GRAB_NEXUS_GATEWAY)")
    ap.add_argument("--sources", default=DEFAULT_SOURCES,
                    help="comma-separated search sources for title lookup")
    args = ap.parse_args()

    if bool(args.query) == bool(args.batch):
        ap.error("provide either a query or --batch FILE (not both)")

    if args.batch:
        citations = _parse_batch_file(args.batch)
        if not citations:
            ap.error(f"no citations found in {args.batch}")
        _run_batch(citations, args)
        sys.exit(0)

    result = asyncio.run(
        grab(args.query, args.out, shadow_sources=_shadow_sources_from_args(args), sources=args.sources)
    )
    for line in _format_result(result, args.query):
        print(line)
    sys.exit(0 if result["success"] else (2 if result.get("ambiguous") else 1))


if __name__ == "__main__":
    main()
