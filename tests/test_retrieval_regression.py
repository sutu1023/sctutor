import pytest

from sctutor.retrieval import reciprocal_rank_fusion


def test_rrf_nominee_does_not_lose_known_rank_at_keyword_cutoff():
    # A semantic hit stays rank 1. Four newly indexed documents move its
    # keyword rank from 19 to 23: losing one entire route is not justified.
    lexical_before = list(range(19, 0, -1))
    semantic_before = [0.0] * 18 + [1.0]
    before = reciprocal_rank_fusion((lexical_before, semantic_before))
    lexical_after = [30, 29, 28, 27] + lexical_before
    semantic_after = [0.0] * 4 + semantic_before
    after = reciprocal_rank_fusion((lexical_after, semantic_after))
    assert before[18] == pytest.approx(1 / 79 + 1 / 61)
    assert after[22] == pytest.approx(1 / 83 + 1 / 61)
    assert before[18] - after[22] < .001
    assert after[22] > 1 / 61  # Still receives its known keyword-rank contribution.


def test_rrf_does_not_expand_candidate_budget_or_reward_missing_branch():
    lexical = list(range(100, 0, -1))
    semantic = list(reversed(lexical))
    scores = reciprocal_rank_fusion((lexical, semantic))
    assert set(scores) == set(range(20)) | set(range(80, 100))
    assert 50 not in scores
    assert scores[0] == pytest.approx(1 / 61 + 1 / 160)
    only_one = reciprocal_rank_fusion(([2, 0, -1], [0, 0, 0]))
    assert only_one == {0: 1 / 61}
    assert reciprocal_rank_fusion(([], [])) == {}


def test_rrf_stable_ties_and_configurable_candidate_limit():
    first = reciprocal_rank_fusion(([1, 1, 1, 1], [1, 1, 1, 1]), limit=2)
    assert first == {0: 2 / 61, 1: 2 / 62}
    assert reciprocal_rank_fusion(([1, 1, 1, 1], [1, 1, 1, 1]), limit=2) == first
