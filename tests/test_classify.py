from grab.classify import classify


def test_arxiv_plain():
    assert classify("1706.03762") == ("arxiv", "1706.03762")


def test_arxiv_versioned():
    assert classify("1706.03762v5") == ("arxiv", "1706.03762v5")


def test_arxiv_prefix():
    assert classify("arXiv:1706.03762") == ("arxiv", "1706.03762")


def test_arxiv_url():
    assert classify("https://arxiv.org/abs/1706.03762") == ("arxiv", "1706.03762")


def test_arxiv_old_style():
    assert classify("arXiv:hep-th/9901001") == ("arxiv", "hep-th/9901001")


def test_doi():
    assert classify("10.1038/nature14539") == ("doi", "10.1038/nature14539")


def test_doi_url():
    assert classify("https://doi.org/10.1038/nature14539") == ("doi", "10.1038/nature14539")


def test_pmid():
    assert classify("26017442") == ("pmid", "26017442")


def test_title():
    assert classify("Attention is all you need") == ("title", "Attention is all you need")
