"""Classify a raw user input into (kind, normalized_value).

kind is one of: "doi", "arxiv", "pmid", "title". Pure functions, no network.
"""
import re

_DOI = re.compile(r'(10\.\d{4,9}/[^\s"\'<>]+)', re.I)
_ARXIV_URL = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)', re.I)
_ARXIV_NEW = re.compile(r'(?:arxiv:)?\s*(\d{4}\.\d{4,5}(?:v\d+)?)\Z', re.I)
_ARXIV_OLD = re.compile(r'arxiv:\s*([a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?)\Z', re.I)
_PMID = re.compile(r'\d{1,8}\Z')


def classify(raw: str):
    s = raw.strip()

    m = _ARXIV_URL.search(s)
    if m:
        return ("arxiv", m.group(1))

    m = _DOI.search(s)
    if m:
        return ("doi", m.group(1).rstrip('.,);'))

    m = _ARXIV_NEW.match(s) or _ARXIV_OLD.match(s)
    if m:
        return ("arxiv", m.group(1))

    # A bare arXiv id always carries a dot (NNNN.NNNNN); a PMID never does.
    if _PMID.match(s):
        return ("pmid", s)

    return ("title", s)
