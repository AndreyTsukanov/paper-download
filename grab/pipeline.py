"""Orchestration: classify input, download via paper_search_mcp, then verify.

`Grabber` wires `TitleMatcher` (disambiguation) and `PdfVerifier` (content check)
around the vendored download chain (source-native -> OA repos -> Unpaywall ->
optional Sci-Hub), which we reuse verbatim and never reimplement. `grab()` is a
thin module-level wrapper kept for the CLI's import.
"""
import os
import re
import json
import time
import html
import asyncio

from grab._vendor.paper_search_mcp import server as ps

from .classify import classify
from .llm import LLMClient
from .disambiguate import TitleMatcher
from .verify import PdfVerifier

DEFAULT_SOURCES = "arxiv,semantic,crossref"
DEFAULT_SCIHUB_URL = "https://sci-hub.ru"  # sci-hub.se no longer resolves; override via GRAB_SCIHUB_URL


def _ok(result) -> bool:
    return isinstance(result, str) and os.path.isfile(result)


def _finalize(result, r):
    if _ok(r):
        result["success"] = True
        result["path"] = r
        result["size"] = os.path.getsize(r)
    else:
        result["errors"].append(str(r))
    return result


_VIA_PREFIXES = (
    ("unpaywall_", "unpaywall"), ("oa_", "openalex/crossref"), ("fatcat_", "fatcat"),
    ("scidb_", "scidb"), ("nexus_", "nexus"), ("europepmc_", "europepmc"),
    ("openaire_", "openaire"), ("core_", "core"), ("pmc_", "pmc"),
)


def _infer_via(path, kind):
    """Best-effort label for which source produced the PDF (from the filename prefix).

    Shadow downloads are self-identifying: SciDB/Nexus files are prefixed, and a bare
    8-hex hash prefix is Sci-Hub's naming — so the three shadow sources stay distinct.
    """
    name = os.path.basename(path or "")
    for prefix, label in _VIA_PREFIXES:
        if name.startswith(prefix):
            return label
    if re.match(r"^[0-9a-f]{8}_", name):
        return "scihub"
    return kind if kind in ("arxiv", "pmid", "doi") else "source-native"


class Grabber:
    """Resolve one query to one PDF: classify -> match -> download -> verify -> log."""

    def __init__(self, save_path, *, shadow_sources=None, sources=DEFAULT_SOURCES,
                 scihub_base_url=None, llm=None, matcher=None, verifier=None):
        self.save_path = os.path.expanduser(save_path)
        self.shadow_sources = list(shadow_sources or [])
        self.sources = sources
        self.scihub_base_url = (scihub_base_url or os.environ.get("GRAB_SCIHUB_URL")
                                or DEFAULT_SCIHUB_URL)
        llm = llm or LLMClient()
        self.matcher = matcher or TitleMatcher(llm)
        self.verifier = verifier or PdfVerifier(llm)

    async def run(self, query) -> dict:
        os.makedirs(self.save_path, exist_ok=True)
        kind, value = classify(query)
        result = {
            "input": query, "kind": kind, "value": value,
            "success": False, "path": None, "size": None,
            "matched": None, "ambiguous": False, "candidates": [], "errors": [], "chain": [],
        }

        if kind == "arxiv":
            r = await self._download("arxiv", paper_id=value)
        elif kind == "doi":
            r = await self._download("crossref", paper_id=value, doi=value)
        elif kind == "pmid":
            r = await self._download("pubmed", paper_id=value)
            if not _ok(r):
                result["errors"].append(f"pubmed: {r}")
                r = await asyncio.to_thread(ps.semantic_searcher.download_pdf, value, self.save_path)
        else:  # title
            return await self._grab_title(value, result)

        _finalize(result, r)
        result["chain"] = getattr(self, "_last_chain", [])

        # Content-verify the identifier flows too (title flow already does this in
        # _grab_title). We have no query title in hand, so fetch the canonical one
        # from the identifier and reuse PdfVerifier. Guard: skip when no title was
        # obtained — verifying against an empty title would falsely reject.
        if result["success"] and kind in ("doi", "pmid"):
            title, authors = await self._canonical_meta(kind, value)
            if title.strip():
                await self._verify_downloaded(result, title, authors, title)

        self._log(result)
        return result

    async def _canonical_meta(self, kind, value):
        """Best-effort (title, authors) for an identifier; ("", "") if unavailable.

        Reuses the vendored lookups (crossref-by-DOI / PubMed search). Any failure
        degrades to ("", ""), which makes the caller skip verification rather than
        reject a file against an empty expected title.
        """
        try:
            if kind == "doi":
                meta = await ps.get_crossref_paper_by_doi(value)
                return (meta.get("title") or "", meta.get("authors") or "")
            if kind == "pmid":
                papers = await asyncio.to_thread(ps.pubmed_searcher.search, value, 1)
                if papers:
                    p = papers[0]
                    return (getattr(p, "title", "") or "",
                            "; ".join(getattr(p, "authors", []) or []))
        except Exception:
            pass
        return ("", "")

    async def _download(self, source, *, paper_id="", doi="", title=""):
        self._last_chain = []  # filled by download_with_fallback (source-by-source outcomes)
        return await ps.download_with_fallback(
            source=source, paper_id=paper_id, doi=doi, title=title,
            save_path=self.save_path, shadow_sources=self.shadow_sources,
            scihub_base_url=self.scihub_base_url, trace=self._last_chain,
        )

    async def _grab_title(self, title, result):
        res = await ps.search_papers(query=title, max_results_per_source=3, sources=self.sources)
        papers = res.get("papers", [])
        if not papers:
            res = await ps.search_papers(query=title, max_results_per_source=3, sources="all")
            papers = res.get("papers", [])
        if not papers:
            result["errors"].append("no search results")
            self._log(result)
            return result

        pick, confidence, method, ranked = await self.matcher.choose(title, papers)
        clean_title = html.unescape(pick.get("title") or "")
        result["matched"] = {
            "title": clean_title, "source": pick.get("source"),
            "doi": pick.get("doi"), "confidence": confidence, "method": method,
        }

        if method == "lowconf":
            result["ambiguous"] = True
            result["candidates"] = [
                {"title": html.unescape(p.get("title") or ""), "doi": p.get("doi"),
                 "source": p.get("source"), "score": round(s, 3)}
                for s, p in self.matcher.visible_candidates(ranked)
            ]
            result["errors"].append(
                "ambiguous title match; not downloading. "
                "Re-run with a DOI/arXiv ID, or set GRAB_LLM_PROVIDER in .env so LLM can choose."
            )
            self._log(result)
            return result

        r = await self._download(
            pick.get("source", ""), paper_id=pick.get("paper_id", "") or "",
            doi=pick.get("doi", "") or "", title=clean_title,
        )
        _finalize(result, r)
        result["chain"] = getattr(self, "_last_chain", [])

        if result["success"]:
            await self._verify_downloaded(result, clean_title, pick.get("authors", "") or "", title)
        else:
            self._note_undownloadable(result)

        self._log(result)
        return result

    async def _verify_downloaded(self, result, title, authors, query):
        """Confirm the saved PDF is the requested paper; discard it if it is not."""
        vr = await asyncio.to_thread(self.verifier.verify, result["path"], title, authors, query)
        result["verify"] = {"status": vr.status, "score": round(vr.score, 3), "reason": vr.reason}
        if vr.status == "rejected":
            try:
                os.remove(result["path"])
            except OSError:
                pass
            result["success"] = False
            result["path"] = None
            result["size"] = None
            result["errors"].append(
                f"content verification failed ({vr.reason}); discarded likely-wrong PDF"
            )

    @staticmethod
    def _note_undownloadable(result):
        """When a confident match exists but no PDF was obtained, say so plainly."""
        matched = result.get("matched")
        if matched and matched.get("doi"):
            result["errors"].append(
                f"matched '{matched['title']}' ({matched['doi']}) but not OA-downloadable "
                "(paywalled or not OA-indexed)"
            )

    def _log(self, result):
        via = _infer_via(result.get("path"), result.get("kind")) if result.get("success") else None
        record = {**result, "via": via, "ts": round(time.time())}
        with open(os.path.join(self.save_path, "manifest.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def grab(query, save_path, *, shadow_sources=None, sources=DEFAULT_SOURCES, scihub_base_url=None):
    """Thin wrapper kept for the CLI: build a Grabber and run one query."""
    grabber = Grabber(save_path, shadow_sources=shadow_sources, sources=sources,
                      scihub_base_url=scihub_base_url)
    return await grabber.run(query)
