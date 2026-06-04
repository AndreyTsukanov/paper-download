"""R4: Unpaywall multi-location ordering + CLI batch-file parsing (offline)."""
import os
import tempfile

from grab._vendor.paper_search_mcp.academic_platforms.unpaywall import UnpaywallResolver
from grab._vendor.paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
from grab.cli import _parse_batch_file


def _resolver_with(payload):
    r = UnpaywallResolver(email="x@example.com")
    r._fetch_doi_record = lambda doi: payload  # stub: no network
    return r


def test_oa_urls_repository_first():
    payload = {
        "best_oa_location": {"host_type": "publisher",
                             "url_for_pdf": "https://pub.example/paper.pdf"},
        "oa_locations": [
            {"host_type": "publisher", "url_for_pdf": "https://pub.example/paper.pdf"},
            {"host_type": "repository", "url_for_pdf": "https://repo.example/bitstream.pdf"},
        ],
    }
    urls = _resolver_with(payload).resolve_oa_pdf_urls("10.1/x")
    assert urls == ["https://repo.example/bitstream.pdf", "https://pub.example/paper.pdf"]


def test_oa_urls_prefers_url_for_pdf_and_handles_empty():
    payload = {
        "best_oa_location": {"host_type": "repository", "url": "https://repo.example/landing"},
        "oa_locations": [
            {"host_type": "repository", "url": "https://repo.example/landing"},
        ],
    }
    urls = _resolver_with(payload).resolve_oa_pdf_urls("10.1/x")
    assert urls == ["https://repo.example/landing"]  # falls back to url, de-duped
    assert _resolver_with({}).resolve_oa_pdf_urls("10.1/x") == []
    assert _resolver_with(None).resolve_oa_pdf_urls("10.1/x") == []


def test_batch_parse_blank_line_separated():
    content = ("First J. A multi-line citation that\nwraps across two lines. 2020\n\n"
               "\n\nSecond author. Another paper. 2021\n\n")
    p = tempfile.mktemp(suffix=".txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        cites = _parse_batch_file(p)
        assert len(cites) == 2
        assert cites[0] == "First J. A multi-line citation that wraps across two lines. 2020"
        assert cites[1] == "Second author. Another paper. 2021"
    finally:
        os.remove(p)


# --- Track 2: OpenAlex OA-location resolver (offline, stubbed session) ---

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self._status = status_code
        self.last_url = None

    def get(self, url, params=None, timeout=None):
        self.last_url = url
        return _FakeResp(self._payload, self._status)


def _openalex_with(payload, status_code=200):
    s = OpenAlexSearcher()
    s.session = _FakeSession(payload, status_code)
    return s


def test_openalex_repository_first_and_dedup():
    payload = {
        "best_oa_location": {"pdf_url": "https://pub.example/x.pdf",
                             "version": "publishedVersion", "source": {"type": "journal"}},
        "locations": [
            {"pdf_url": "https://pub.example/x.pdf",
             "version": "publishedVersion", "source": {"type": "journal"}},
            {"pdf_url": "https://repo.example/green.pdf",
             "version": "acceptedVersion", "source": {"type": "repository"}},
        ],
        "open_access": {"oa_url": "https://repo.example/green.pdf"},
    }
    urls = _openalex_with(payload).resolve_oa_pdf_urls("10.1/abc")
    assert urls == ["https://repo.example/green.pdf", "https://pub.example/x.pdf"]


def test_openalex_doi_prefix_stripped_in_request():
    s = _openalex_with({"locations": []})
    s.resolve_oa_pdf_urls("https://doi.org/10.1/ABC")
    assert s.session.last_url == "https://api.openalex.org/works/https://doi.org/10.1/abc"


def test_openalex_non_200_and_empty_doi():
    assert _openalex_with({}, status_code=404).resolve_oa_pdf_urls("10.1/abc") == []
    assert OpenAlexSearcher().resolve_oa_pdf_urls("") == []
