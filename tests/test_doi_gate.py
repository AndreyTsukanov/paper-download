import types

from grab._vendor.paper_search_mcp import server as s


def _p(doi="", title=""):
    return types.SimpleNamespace(doi=doi, title=title, pdf_url="http://x/y.pdf", paper_id="1")


def test_exact_doi_matches():
    assert s._candidate_matches(_p(doi="10.1056/NEJMoa1602252"), "10.1056/nejmoa1602252", "") is True


def test_doi_prefix_stripped():
    assert s._candidate_matches(
        _p(doi="https://doi.org/10.1056/nejmoa1602252"), "doi:10.1056/NEJMoa1602252", "") is True


def test_different_doi_rejected():
    assert s._candidate_matches(_p(doi="10.9999/other"), "10.1056/nejmoa1602252", "") is False


def test_no_candidate_doi_falls_back_to_title():
    title = "Nivolumab for Recurrent Squamous-Cell Carcinoma of the Head and Neck"
    assert s._candidate_matches(_p(doi="", title=title), "10.1056/nejmoa1602252", title) is True
    assert s._candidate_matches(
        _p(doi="", title="A cohort study of low birth weight in Ghana"),
        "10.1056/nejmoa1602252", title) is False
