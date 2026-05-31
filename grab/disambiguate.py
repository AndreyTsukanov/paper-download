"""Disambiguate a fuzzy title query into the right paper.

Scoring is title *containment* in the (citation-style) query: the fraction of a
candidate title's tokens present in the query, which stays high even when the
query carries authors/journal/year cruft. A confident, clearly-ahead top match
is accepted with no LLM; otherwise one cheap `LLMClient.pick()` breaks the tie.
"""
import re
import html
import asyncio
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Tuple

from .llm import LLMClient


def normalize(s: str) -> str:
    """Lowercased ascii+cyrillic tokens (HTML entities decoded), space-joined."""
    s = html.unescape(s or "")
    return " ".join(re.findall(r"[a-z0-9Ѐ-ӿ]+", s.lower()))


# English function words that inflate containment for off-topic titles (e.g. an
# arXiv physics paper "matching" an oncology query only on the/of/and). Dropped,
# along with single-char tokens, before scoring. Cyrillic is left intact (recall,
# not stopword noise, was the issue there).
STOPWORDS = frozenset(
    "the of and or a an in on for to with versus vs as at by from into is are be".split()
)


def content_tokens(tokens) -> set:
    """Tokens worth scoring on: drop stopwords and single-char noise."""
    return {t for t in tokens if len(t) > 1 and t not in STOPWORDS}


class TitleMatcher:
    CONFIDENT = 0.90          # accept without LLM when top score is at least this
    GAP = 0.10               # ...and at least this far ahead of the runner-up
    MIN_TITLE_TOKENS = 4     # shorter titles over-score on containment; damp them
    MAX_CANDIDATES = 8       # how many to hand the LLM
    CANDIDATE_FLOOR = 0.20   # hide near-zero off-topic search noise from surfaced candidates

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    def containment(self, query_tokens, title) -> float:
        t_tokens = content_tokens(normalize(title).split())
        if not t_tokens:
            return 0.0
        covered = len(t_tokens & query_tokens) / len(t_tokens)
        if len(t_tokens) < self.MIN_TITLE_TOKENS:
            covered *= 0.6
        return covered

    def rank(self, query, papers) -> List[Tuple[float, Dict]]:
        """Return [(confidence, paper), ...] best-first (difflib breaks ties)."""
        q = normalize(query)
        q_tokens = content_tokens(q.split())
        enriched = [
            (self.containment(q_tokens, p.get("title", "")),
             SequenceMatcher(None, q, normalize(p.get("title", ""))).ratio(),
             p)
            for p in papers
        ]
        enriched.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [(score, p) for score, _ratio, p in enriched]

    def visible_candidates(self, ranked, k: int = 5):
        """Candidates worth surfacing: drop near-zero noise, but always show >=1."""
        kept = [(s, p) for s, p in ranked if s >= self.CANDIDATE_FLOOR][:k]
        return kept or ranked[:1]

    async def choose(self, query, papers):
        """Return (paper, confidence, method, ranked). method ∈ {fuzzy, llm, lowconf}.

        "fuzzy"/"llm" are trustworthy enough to download; "lowconf" means the
        caller should surface candidates instead of guessing.
        """
        ranked = self.rank(query, papers)
        top_score, top = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0

        if top_score >= self.CONFIDENT and (top_score - second) >= self.GAP:
            return top, round(top_score, 3), "fuzzy", ranked

        if self.llm.has_key():
            candidates = [p for _, p in ranked[:self.MAX_CANDIDATES]]
            idx = await asyncio.to_thread(self.llm.pick, query, candidates)
            if idx is not None and 0 <= idx < len(candidates):
                chosen = candidates[idx]
                score = next(s for s, p in ranked if p is chosen)
                return chosen, round(score, 3), "llm", ranked

        return top, round(top_score, 3), "lowconf", ranked
