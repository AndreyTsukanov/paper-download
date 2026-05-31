from grab._vendor.paper_search_mcp import server as s


def test_citation_pdf_url_meta():
    html = (b'<html><head>'
            b'<meta name="citation_pdf_url" content="https://x.org/a/b.pdf">'
            b'</head></html>')
    assert s._extract_pdf_link(html, "https://x.org/page") == "https://x.org/a/b.pdf"


def test_dspace_bitstream_relative():
    html = b'<html><body><a href="/bitstreams/uuid/download">PDF</a></body></html>'
    base = "https://research.dial.uclouvain.be/handle/2078.5/125024"
    assert s._extract_pdf_link(html, base) == \
        "https://research.dial.uclouvain.be/bitstreams/uuid/download"


def test_no_pdf_link():
    html = b'<html><body><a href="/about">About</a><a href="/cite.ris">RIS</a></body></html>'
    assert s._extract_pdf_link(html, "https://x.org") is None
