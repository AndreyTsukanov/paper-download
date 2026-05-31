import os
import asyncio
import tempfile

from grab.verify import PdfVerifier
from grab.pipeline import Grabber


def _tmp(content: bytes, suffix=".pdf"):
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return p


def test_looks_like_pdf_rejects_html():
    p = _tmp(b"<!DOCTYPE html>\n<html><body>error</body></html>" + b" " * 2000)
    try:
        assert PdfVerifier.looks_like_pdf(p) is False
    finally:
        os.remove(p)


def test_looks_like_pdf_accepts_magic():
    p = _tmp(b"%PDF-1.5\n" + b"0" * 2000)
    try:
        assert PdfVerifier.looks_like_pdf(p) is True
    finally:
        os.remove(p)


def test_verify_rejects_non_pdf_without_llm():
    p = _tmp(b"<html>not a pdf</html>" + b" " * 2000)
    try:
        r = PdfVerifier().verify(p, "Some Long Title Words Here", "Author A", "q")
        assert r.status == "rejected"
    finally:
        os.remove(p)


def test_score_high_and_low():
    text = ("Nivolumab for Recurrent Squamous Cell Carcinoma of the Head and Neck\n"
            "Robert Ferris et al.  New England Journal of Medicine")
    hi = PdfVerifier.score(
        text, "Nivolumab for Recurrent Squamous Cell Carcinoma of the Head and Neck", "Ferris")
    lo = PdfVerifier.score(
        text, "A cohort study of low birth weight in the first year of life Ghana", "Smith")
    assert hi >= 0.6
    assert lo < 0.3


def test_doi_flow_skips_verify_without_title():
    """DOI flow with no canonical title (e.g. not in crossref) must NOT reject the
    file — verifying against an empty title would spuriously delete a good PDF."""
    out = tempfile.mkdtemp()
    pdf = os.path.join(out, "paper.pdf")
    with open(pdf, "wb") as f:
        f.write(b"%PDF-1.5\n" + b"0" * 4000)  # real magic, but no extractable title

    g = Grabber(out)
    g._download = lambda *a, **k: _coro(pdf)           # stub network download
    g._canonical_meta = lambda *a, **k: _coro(("", ""))  # title lookup unavailable

    res = asyncio.run(g.run("10.9999/not-in-crossref"))
    assert res["success"] is True
    assert res["path"] == pdf
    assert os.path.isfile(pdf)        # not deleted
    assert "verify" not in res        # verification skipped, not rejected
    os.remove(pdf)


async def _coro(value):
    return value
