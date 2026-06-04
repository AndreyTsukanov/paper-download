"""Fatcat / Internet Archive Scholar PDF resolver (grab addition, not upstream).

Fatcat is the open catalog behind scholar.archive.org. It preserves open-access
— and many once-OA, now-dark — PDFs on archive.org. We look a release up by its
exact DOI and return the preserved PDF URLs (archive.org / web.archive.org first),
to be fetched through the normal `_download_from_url` path. Exact-DOI lookup means
no fuzzy DOI-gate is needed; content verification still backstops. This is a legal
source (Internet Archive), so it lives in the OA chain, not behind a shadow flag.

The Fatcat API has occasional downtime; every error degrades to an empty list so
the surrounding download chain simply moves on.
"""
from typing import List
import logging
import requests

from ..config import FATCAT_CONNECT_TIMEOUT, FATCAT_READ_TIMEOUT, FATCAT_FAILURE_LIMIT

logger = logging.getLogger(__name__)


class FatcatResolver:
    """Resolve preserved (archive.org) PDF URLs for a DOI via the Fatcat API."""

    BASE_URL = "https://api.fatcat.wiki/v0"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "paper-search-mcp/1.0 (grab; mailto:openags@example.com)",
            "Accept": "application/json",
        })
        # Circuit breaker: api.fatcat.wiki has real downtime. After a couple of
        # connection failures, stop trying for the rest of the process so a whole
        # batch doesn't pay the per-paper timeout when Fatcat is simply unreachable.
        self._consecutive_failures = 0
        self._disabled = False
        self._last_unreachable = False  # was the most recent lookup a connection failure?

    def resolve_pdf_urls(self, doi: str) -> List[str]:
        """All preserved PDF URLs Fatcat knows for a DOI; archive.org first, deduped."""
        if self._disabled:
            return []
        doi = (doi or "").strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
                break
        if not doi:
            return []

        try:
            resp = self.session.get(
                f"{self.BASE_URL}/release/lookup",
                params={"doi": doi, "expand": "files"},
                timeout=(FATCAT_CONNECT_TIMEOUT, FATCAT_READ_TIMEOUT),
            )
        except Exception as e:
            self._consecutive_failures += 1
            self._last_unreachable = True
            if self._consecutive_failures >= FATCAT_FAILURE_LIMIT:
                self._disabled = True
                logger.warning("Fatcat unreachable; disabling it for this run.")
            else:
                logger.warning(f"Fatcat lookup error for {doi}: {e}")
            return []

        self._consecutive_failures = 0  # reachable: the host answered
        self._last_unreachable = False
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []

        urls: List[str] = []
        for f in data.get("files") or []:
            if not isinstance(f, dict):
                continue
            mimetype = (f.get("mimetype") or "").lower()
            candidates = [
                (u.get("rel") or "", u["url"])
                for u in (f.get("urls") or [])
                if isinstance(u, dict) and u.get("url")
            ]
            if not candidates:
                continue
            # Trust only entries that look like a PDF; the download step revalidates.
            looks_pdf = "pdf" in mimetype or any(url.lower().endswith(".pdf") for _, url in candidates)
            if not looks_pdf:
                continue
            # archive.org / web.archive.org copies are the reliably fetchable ones.
            for _, url in sorted(candidates, key=lambda item: 0 if "archive.org" in item[1] else 1):
                if url not in urls:
                    urls.append(url)
        return urls
