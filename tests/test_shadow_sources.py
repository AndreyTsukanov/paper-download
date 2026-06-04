"""R6: Fatcat parse, SciDB PDF-link parse, --shadow flag mapping, via inference (offline)."""
import os
import types
import tempfile

from grab._vendor.paper_search_mcp.academic_platforms.fatcat import FatcatResolver
from grab._vendor.paper_search_mcp.academic_platforms.scidb import SciDBFetcher
from grab.cli import _shadow_sources_from_args
from grab.pipeline import _infer_via


# --- Fatcat resolver (stubbed session, no network) ---

class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload, status_code=200):
        self._payload, self._status = payload, status_code

    def get(self, url, params=None, timeout=None):
        return _Resp(self._payload, self._status)


def _fatcat(payload, status_code=200):
    r = FatcatResolver()
    r.session = _Session(payload, status_code)
    return r


def test_fatcat_archive_first_pdf_only_dedup():
    payload = {"files": [
        {"mimetype": "application/pdf", "urls": [
            {"rel": "web", "url": "https://pub.example/x.pdf"},
            {"rel": "webarchive", "url": "https://web.archive.org/web/1/https://pub.example/x.pdf"},
        ]},
        {"mimetype": "text/html", "urls": [{"rel": "web", "url": "https://pub.example/landing"}]},
        {"mimetype": "application/pdf", "urls": [
            {"rel": "webarchive", "url": "https://web.archive.org/web/1/https://pub.example/x.pdf"}]},
    ]}
    urls = _fatcat(payload).resolve_pdf_urls("10.1/x")
    assert urls == ["https://web.archive.org/web/1/https://pub.example/x.pdf",
                    "https://pub.example/x.pdf"]  # archive-first, html skipped, deduped


def test_fatcat_non_200_and_empty():
    assert _fatcat({}, status_code=404).resolve_pdf_urls("10.1/x") == []
    assert FatcatResolver().resolve_pdf_urls("") == []


# --- SciDB PDF-link extraction (pure parse) + direct-PDF download ---

def test_scidb_extract_candidates_order_and_filtering():
    html = (b'<html><head><meta name="citation_pdf_url" content="https://m.example/a.pdf"></head>'
            b'<body><iframe src="/viewer/embed.pdf"></iframe>'
            b'<a href="/scimag/10.1/x.pdf">file</a>'
            b'<a href="/about">About</a>'
            b'<a href="https://partner.example/dl">Download from partner</a>'
            b'</body></html>')
    f = SciDBFetcher(output_dir=tempfile.mkdtemp())
    cands = f._extract_pdf_candidates(html, "https://annas-archive.se/scidb/10.1/x")
    assert cands[0] == "https://m.example/a.pdf"                      # meta first
    assert "https://annas-archive.se/viewer/embed.pdf" in cands       # iframe (relative resolved)
    assert "https://annas-archive.se/scimag/10.1/x.pdf" in cands      # .pdf anchor
    assert "https://partner.example/dl" in cands                      # matched by link text
    assert all("/about" not in c for c in cands)                      # noise dropped


def test_scidb_direct_pdf_saves_prefixed_file():
    f = SciDBFetcher(output_dir=tempfile.mkdtemp())

    class _PdfResp:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        content = b"%PDF-1.5\n" + b"x" * 400
        url = "https://annas-archive.se/scidb/10.1/x"

    f._get = lambda url: _PdfResp()
    path = f.download_pdf("10.1/x")
    assert path and os.path.basename(path).startswith("scidb_") and os.path.isfile(path)
    os.remove(path)


# --- flag mapping + via inference ---

def test_shadow_flag_mapping():
    def mk(scihub, shadow):
        return _shadow_sources_from_args(types.SimpleNamespace(scihub=scihub, shadow=shadow))
    assert mk(False, False) == []
    assert mk(True, False) == ["scihub"]
    assert mk(False, True) == ["scihub", "scidb", "nexus"]
    assert mk(True, True) == ["scihub", "scidb", "nexus"]  # --shadow wins


def test_infer_via():
    assert _infer_via("/d/scidb_ab12cd34_10.1.pdf", "doi") == "scidb"
    assert _infer_via("/d/nexus_ab12cd34_10.1.pdf", "doi") == "nexus"
    assert _infer_via("/d/deadbeef_paper.pdf", "doi") == "scihub"   # bare 8-hex = Sci-Hub
    assert _infer_via("/d/fatcat_10.1.pdf", "doi") == "fatcat"
    assert _infer_via("/d/unpaywall_10.1.pdf", "doi") == "unpaywall"
    assert _infer_via("/d/2605.28773v1.pdf", "arxiv") == "arxiv"
