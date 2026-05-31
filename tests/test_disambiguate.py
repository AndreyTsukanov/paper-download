from grab import disambiguate as d
from grab.disambiguate import TitleMatcher


def test_normalize_keeps_cyrillic():
    out = d.normalize("Практические Рекомендации RUSSCO").split()
    assert "практические" in out
    assert "russco" in out


def test_normalize_unescapes_entities():
    out = d.normalize("LUX-Head &amp; Neck 1").split()
    assert "neck" in out
    assert "amp" not in out


def test_rank_correct_high_wrong_low():
    query = ("Ferris R., Blumenschein G. et al. Nivolumab for recurrent squamous-cell "
             "carcinoma of the head and neck. N Engl J Med 2016;375:1856-67")
    papers = [
        {"title": "Nivolumab for Recurrent Squamous-Cell Carcinoma of the Head and Neck"},
        {"title": "A cohort study of low birth weight and health outcomes in the first year of life, Ghana"},
    ]
    ranked = TitleMatcher().rank(query, papers)
    assert ranked[0][1]["title"].startswith("Nivolumab")
    assert ranked[0][0] >= 0.9
    assert ranked[-1][0] < 0.3


def test_visible_candidates_drops_noise():
    tm = TitleMatcher()
    ranked = [(0.889, {"title": "real"}), (0.125, {"title": "physics junk"}), (0.0, {"title": "more junk"})]
    vis = tm.visible_candidates(ranked)
    assert all(s >= tm.CANDIDATE_FLOOR for s, _ in vis)
    assert len(vis) == 1


def test_visible_candidates_never_empty():
    tm = TitleMatcher()
    ranked = [(0.05, {"title": "x"}), (0.0, {"title": "y"})]
    assert len(tm.visible_candidates(ranked)) == 1


def test_stopwords_dont_inflate_offtopic():
    """An off-topic arXiv physics title must score ~0 on an onco query (used to be
    0.25-0.30 from the/of/and/single-char overlap, clearing CANDIDATE_FLOOR)."""
    query = ("Burtness B, Harrington KJ, et al. Pembrolizumab alone or with chemotherapy "
             "versus cetuximab for recurrent or metastatic squamous cell carcinoma of the "
             "head and neck (KEYNOTE-048). Lancet 2019;394:1915-1928")
    papers = [
        {"title": "Phase 3 trial of pembrolizumab in recurrent/metastatic head and neck squamous cell carcinoma: KEYNOTE-048"},
        {"title": "Expected Performance of the ATLAS Experiment - Detector, Trigger and Physics"},
        {"title": "Observation of the rare B0s decay from the combined analysis of CMS and LHCb data"},
    ]
    ranked = TitleMatcher().rank(query, papers)
    by_title = {p["title"][:10]: s for s, p in ranked}
    assert by_title["Phase 3 tr"] >= 0.5
    assert by_title["Expected P"] < TitleMatcher.CANDIDATE_FLOOR
    assert by_title["Observatio"] < TitleMatcher.CANDIDATE_FLOOR
