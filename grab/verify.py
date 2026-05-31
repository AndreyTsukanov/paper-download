"""Verify a downloaded PDF really is the paper we asked for.

Deterministic checks run first (is it a real PDF? does its first-page text
contain the expected title/author?). A cheap LLM yes/no is consulted ONLY for the
gray zone, and only on valid extractable text — never to rescue a broken or wrong
download into a success. The LLM can confirm or veto a gray-zone match; it can
neither override a deterministic reject nor judge from empty/garbage input.
"""
import os
from dataclasses import dataclass
from typing import Optional

from . import disambiguate
from .llm import LLMClient


@dataclass
class VerifyResult:
    status: str   # "verified" | "unverified" | "rejected"
    score: float
    reason: str


class PdfVerifier:
    MIN_PDF_BYTES = 1024     # smaller than this is almost certainly an error page / stub
    MIN_TEXT_CHARS = 200     # below this we can't verify content deterministically
    ACCEPT_SCORE = 0.60      # title coverage at/above this -> verified
    REJECT_SCORE = 0.30      # title coverage below this -> rejected (different paper)

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    @staticmethod
    def looks_like_pdf(path) -> bool:
        """True only if the file starts with %PDF and is large enough to be real."""
        try:
            if os.path.getsize(path) < PdfVerifier.MIN_PDF_BYTES:
                return False
            with open(path, "rb") as fh:
                return fh.read(5).startswith(b"%PDF")
        except OSError:
            return False

    @staticmethod
    def extract_text(path, max_pages: int = 2) -> str:
        """First-pages text via pypdf; '' on any failure (e.g. scanned PDF)."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
        except Exception:
            return ""

    @staticmethod
    def score(text, title, authors) -> float:
        """How well the PDF text matches the expected paper [0..1].

        Mostly title-token coverage (the title appears on page 1 of the real
        paper); a small bonus if an author token also appears.
        """
        text_tokens = set(disambiguate.normalize(text).split())
        title_tokens = set(disambiguate.normalize(title).split())
        if not title_tokens:
            return 0.0
        coverage = len(title_tokens & text_tokens) / len(title_tokens)
        author_tokens = {w for w in disambiguate.normalize(authors).split() if len(w) >= 4}
        bonus = 0.1 if author_tokens & text_tokens else 0.0
        return min(coverage + bonus, 1.0)

    def verify(self, path, title, authors, query) -> VerifyResult:
        """Decide whether a freshly downloaded PDF is the requested paper.

        Order matters (see module docstring): structural check -> text extraction
        -> deterministic score -> LLM only for the gray band on real text.
        """
        if not self.looks_like_pdf(path):
            return VerifyResult("rejected", 0.0, "not a real PDF (HTML/error page or truncated)")

        text = self.extract_text(path)
        if len(text.strip()) < self.MIN_TEXT_CHARS:
            # DOI gate already vouched for the identifier; keep the file but be honest.
            return VerifyResult("unverified", 0.0, "no extractable text (scanned PDF); DOI already matched")

        s = self.score(text, title, authors)
        if s >= self.ACCEPT_SCORE:
            return VerifyResult("verified", s, "title/author found in PDF text")
        if s < self.REJECT_SCORE:
            return VerifyResult("rejected", s, "PDF content does not match requested title")

        # Gray zone: deterministic check inconclusive. Consult the cheap LLM if we
        # have a key, but only as a veto/confirm on the (valid, non-empty) text.
        if self.llm.has_key():
            verdict = self.llm.verify_match(query, text[:2000])
            if verdict is True:
                return VerifyResult("verified", s, "LLM confirmed match (gray zone)")
            if verdict is False:
                return VerifyResult("rejected", s, "LLM rejected match (gray zone)")
        return VerifyResult("unverified", s, "inconclusive content match; no LLM confirmation")
