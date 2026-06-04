"""Anna's Archive SciDB shadow downloader (grab addition, not upstream).

SciDB (https://annas-archive.*/scidb/<doi>) is Anna's Archive's Sci-Hub-style
DOI viewer. It carries the full Sci-Hub corpus *plus* newer (post-2021) papers
that Sci-Hub's frozen corpus lacks — which is the whole reason to add a shadow
source beyond Sci-Hub. Like Sci-Hub, this is an opt-in, off-by-default source
(enabled via `--shadow`); the downloaded PDF is still content-verified upstream,
so a wrong/corrupt file is discarded rather than saved as a success.

DOI-primary. The mirror domain rotates (.se/.org/.gl/.li/…) and the page markup
changes, so the PDF extraction is deliberately generic (meta tag → embed/iframe →
download/.pdf anchor) and best-effort: any failure returns None and the chain
moves on. Override the base via GRAB_SCIDB_URL.
"""
from pathlib import Path
import os
import re
import hashlib
import logging
from typing import Optional, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..config import SCIDB_TIMEOUT

logger = logging.getLogger(__name__)


class SciDBFetcher:
    """Best-effort SciDB (Anna's Archive) PDF downloader, DOI-primary."""

    def __init__(self, base_url: str = "https://annas-archive.se", output_dir: str = "./downloads"):
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def _normalize_doi(self, identifier: str) -> str:
        doi = (identifier or "").strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix):]
                break
        return doi.strip()

    def _get(self, url: str):
        """GET trying a direct connection first, then GRAB_DOWNLOAD_PROXY on failure."""
        try:
            r = self.session.get(url, timeout=SCIDB_TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                return r
        except Exception as e:
            logger.debug("SciDB direct GET failed for %s: %s", url, e)
        proxy = os.environ.get("GRAB_DOWNLOAD_PROXY")
        if proxy:
            try:
                return self.session.get(url, timeout=SCIDB_TIMEOUT, allow_redirects=True,
                                        proxies={"http": proxy, "https": proxy})
            except Exception as e:
                logger.debug("SciDB proxy GET failed for %s: %s", url, e)
        return None

    @staticmethod
    def _is_pdf(response) -> bool:
        ct = (response.headers.get("content-type") or "").lower()
        return "pdf" in ct or response.content[:5].startswith(b"%PDF")

    def _extract_pdf_candidates(self, html_bytes: bytes, page_url: str) -> List[str]:
        """Ordered candidate PDF URLs found on a SciDB page (generic, best-effort)."""
        candidates: List[str] = []

        def add(href: Optional[str]):
            if href:
                full = urljoin(page_url, href.strip())
                if full not in candidates:
                    candidates.append(full)

        try:
            soup = BeautifulSoup(html_bytes, "html.parser")
        except Exception:
            return candidates

        meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
        if meta and meta.get("content"):
            add(meta["content"])
        for tag in soup.find_all(["embed", "iframe", "object"]):
            add(tag.get("src") or tag.get("data"))
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            low = href.lower()
            text = (a.get_text() or "").lower()
            if (low.endswith(".pdf") or ".pdf?" in low or "/scimag/" in low
                    or "download" in low or "download" in text
                    or "скачать" in text):
                add(href)
        return candidates

    def _save(self, content: bytes, identifier: str) -> str:
        pdf_hash = hashlib.md5(content).hexdigest()[:8]
        clean = re.sub(r"[^\w\-.]", "_", identifier)[:60]
        path = self.output_dir / f"scidb_{pdf_hash}_{clean}.pdf"
        with open(path, "wb") as fh:
            fh.write(content)
        return str(path)

    def download_pdf(self, identifier: str) -> Optional[str]:
        """Download a PDF from SciDB by DOI. Returns the saved path or None.

        Sets ``self.last_status`` (no doi / unreachable / no pdf / ok) so the caller
        can report an honest per-source outcome in the chain trace.
        """
        self.last_status = "no doi"
        doi = self._normalize_doi(identifier)
        if not doi:
            return None

        self.last_status = "unreachable"  # until the page is fetched
        page_url = f"{self.base_url}/scidb/{doi}"
        resp = self._get(page_url)
        if resp is None:
            return None

        self.last_status = "no pdf"
        # SciDB may serve the PDF directly, or an HTML viewer linking to it.
        if self._is_pdf(resp) and len(resp.content) >= 256:
            self.last_status = "ok"
            return self._save(resp.content, doi)

        for candidate in self._extract_pdf_candidates(resp.content, str(resp.url)):
            if candidate == page_url:
                continue
            pdf = self._get(candidate)
            if pdf is not None and self._is_pdf(pdf) and len(pdf.content) >= 256:
                self.last_status = "ok"
                return self._save(pdf.content, doi)
        return None
