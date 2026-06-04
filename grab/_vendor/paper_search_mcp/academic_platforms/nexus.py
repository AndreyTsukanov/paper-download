"""Nexus / STC shadow downloader — EXPERIMENTAL (grab addition, not upstream).

Nexus/STC (Standard Template Construct) is an IPFS-backed shadow library and is
the one source that reliably carries *recent* (post-2021) paywalled papers that
Sci-Hub's frozen corpus and even SciDB may lack. It has **no clean public REST
API** (access is via IPFS/IPNS gateways and Telegram bots), so this connector is
deliberately a thin, configuration-driven best-effort: it does nothing unless you
point GRAB_NEXUS_GATEWAY at a resolver, and any failure returns None so the chain
simply moves on. Opt-in (`--shadow`), off by default; the result is still
content-verified upstream.

GRAB_NEXUS_GATEWAY is a URL template; `{doi}` is substituted (or the DOI is
appended if the placeholder is absent), e.g.:
    GRAB_NEXUS_GATEWAY=https://<ipfs-gateway>/ipns/<stc-key>/{doi}
"""
from pathlib import Path
import os
import re
import hashlib
import logging
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..config import NEXUS_TIMEOUT

logger = logging.getLogger(__name__)


class NexusFetcher:
    """Experimental Nexus/STC PDF fetcher; requires GRAB_NEXUS_GATEWAY to do anything."""

    def __init__(self, gateway: Optional[str] = None, output_dir: str = "./downloads"):
        self.gateway = (gateway if gateway is not None
                        else os.environ.get("GRAB_NEXUS_GATEWAY", "")).strip()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        })

    def _normalize_doi(self, identifier: str) -> str:
        doi = (identifier or "").strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix):]
                break
        return doi.strip()

    def _get(self, url: str):
        try:
            r = self.session.get(url, timeout=NEXUS_TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                return r
        except Exception as e:
            logger.debug("Nexus direct GET failed for %s: %s", url, e)
        proxy = os.environ.get("GRAB_DOWNLOAD_PROXY")
        if proxy:
            try:
                return self.session.get(url, timeout=NEXUS_TIMEOUT, allow_redirects=True,
                                        proxies={"http": proxy, "https": proxy})
            except Exception as e:
                logger.debug("Nexus proxy GET failed for %s: %s", url, e)
        return None

    @staticmethod
    def _is_pdf(response) -> bool:
        ct = (response.headers.get("content-type") or "").lower()
        return "pdf" in ct or response.content[:5].startswith(b"%PDF")

    def _save(self, content: bytes, identifier: str) -> str:
        pdf_hash = hashlib.md5(content).hexdigest()[:8]
        clean = re.sub(r"[^\w\-.]", "_", identifier)[:60]
        path = self.output_dir / f"nexus_{pdf_hash}_{clean}.pdf"
        with open(path, "wb") as fh:
            fh.write(content)
        return str(path)

    def download_pdf(self, identifier: str) -> Optional[str]:
        """Resolve a DOI through the configured Nexus/STC gateway. None if unconfigured.

        Sets ``self.last_status`` so the caller can report an honest per-source outcome.
        """
        if not self.gateway:
            self.last_status = "skipped (no GRAB_NEXUS_GATEWAY)"
            return None
        self.last_status = "no doi"
        doi = self._normalize_doi(identifier)
        if not doi:
            return None

        self.last_status = "unreachable"
        url = (self.gateway.replace("{doi}", doi) if "{doi}" in self.gateway
               else f"{self.gateway.rstrip('/')}/{doi}")
        resp = self._get(url)
        if resp is None:
            return None

        self.last_status = "no pdf"
        if self._is_pdf(resp) and len(resp.content) >= 256:
            self.last_status = "ok"
            return self._save(resp.content, doi)

        # Otherwise look for a single PDF link on the resolved page.
        try:
            soup = BeautifulSoup(resp.content, "html.parser")
        except Exception:
            return None
        for tag in soup.find_all(["a", "embed", "iframe"], href=True) + soup.find_all(["embed", "iframe"]):
            href = tag.get("href") or tag.get("src") or ""
            if href and (".pdf" in href.lower() or "ipfs" in href.lower()):
                pdf = self._get(urljoin(str(resp.url), href.strip()))
                if pdf is not None and self._is_pdf(pdf) and len(pdf.content) >= 256:
                    self.last_status = "ok"
                    return self._save(pdf.content, doi)
        return None
