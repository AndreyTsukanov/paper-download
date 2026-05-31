import os
import re
import sys
import argparse
import asyncio
from pathlib import Path

# Load .env from project root (where pyproject.toml lives)
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

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
    return lines


def _run_batch(citations, args):
    """Run every citation, write a human-readable run.log next to manifest.jsonl."""
    out = os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)
    log_path = os.path.join(out, "run.log")
    n = len(citations)
    counts = {"ok": 0, "ambiguous": 0, "failed": 0}

    with open(log_path, "w", encoding="utf-8") as log:
        header = f"grab batch: {n} citations -> {out}" + (" [--scihub]" if args.scihub else "")
        print(header)
        log.write(header + "\n")
        for i, q in enumerate(citations, 1):
            result = asyncio.run(grab(q, args.out, use_scihub=args.scihub, sources=args.sources))
            if result["success"]:
                mark, key = "✓", "ok"
            elif result.get("ambiguous"):
                mark, key = "?", "ambiguous"
            else:
                mark, key = "✗", "failed"
            counts[key] += 1
            block = [f"[{i}/{n}] {mark} {q[:90]}"] + ["  " + ln for ln in _format_result(result, q)]
            text = "\n".join(block)
            print(text)
            log.write(text + "\n")
        summary = (f"=== done: downloaded {counts['ok']}/{n} · "
                   f"ambiguous {counts['ambiguous']} · failed {counts['failed']} ===")
        print(summary)
        log.write(summary + "\n")
    print(f"(log: {log_path} · manifest: {os.path.join(out, 'manifest.jsonl')})")


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
    ap.add_argument("--scihub", action="store_true", help="allow Sci-Hub fallback")
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
        grab(args.query, args.out, use_scihub=args.scihub, sources=args.sources)
    )
    for line in _format_result(result, args.query):
        print(line)
    sys.exit(0 if result["success"] else (2 if result.get("ambiguous") else 1))


if __name__ == "__main__":
    main()
