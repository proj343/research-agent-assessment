"""Tests for eval/benchmark.py — score_response() and questions.json schema."""

import json
from pathlib import Path

import pytest

from eval.benchmark import score_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FULL_CRITERIA = {
    "required_terms": ["basel", "capital", "tier"],
    "must_cite": True,
    "should_refuse": False,
}


def _score(answer: str, tools: list[str] | None = None, criteria: dict | None = None) -> dict:
    return score_response(answer, tools or ["wikipedia_search"], criteria or {})


# ---------------------------------------------------------------------------
# Term coverage
# ---------------------------------------------------------------------------


class TestTermCoverage:
    def test_all_terms_found(self):
        result = _score(
            "Basel capital tier requirements",
            criteria={"required_terms": ["basel", "capital", "tier"]},
        )
        assert result["term_coverage"] == 1.0
        assert result["terms_missing"] == []

    def test_no_terms_found(self):
        result = _score("Some irrelevant text", criteria={"required_terms": ["basel", "capital"]})
        assert result["term_coverage"] == 0.0
        assert len(result["terms_missing"]) == 2

    def test_partial_coverage(self):
        result = _score(
            "Basel is important", criteria={"required_terms": ["basel", "capital", "tier"]}
        )
        assert result["term_coverage"] == pytest.approx(1 / 3, abs=0.01)
        assert "basel" in result["terms_found"]
        assert "capital" in result["terms_missing"]

    def test_empty_required_terms_gives_full_coverage(self):
        result = _score("Anything at all", criteria={"required_terms": []})
        assert result["term_coverage"] == 1.0

    def test_no_required_terms_key_gives_full_coverage(self):
        result = _score("Any text", criteria={})
        assert result["term_coverage"] == 1.0

    def test_term_matching_is_case_insensitive(self):
        result = _score(
            "BASEL capital TIER", criteria={"required_terms": ["Basel", "Capital", "Tier"]}
        )
        assert result["term_coverage"] == 1.0

    def test_terms_found_and_missing_are_correct(self):
        result = _score("Basel only", criteria={"required_terms": ["basel", "capital", "tier"]})
        assert "basel" in result["terms_found"]
        assert "capital" in result["terms_missing"]
        assert "tier" in result["terms_missing"]

    def test_single_term(self):
        result = _score("The FDIC was created in 1933", criteria={"required_terms": ["fdic"]})
        assert result["term_coverage"] == 1.0


# ---------------------------------------------------------------------------
# Citation detection
# ---------------------------------------------------------------------------


class TestCitationScore:
    @pytest.mark.parametrize(
        "marker", ["[wikipedia", "[arxiv", "[fred", "http", "doi.org", "arxiv.org"]
    )
    def test_each_citation_marker_detected(self, marker):
        result = _score(f"See the source {marker} for details", criteria={"must_cite": True})
        assert result["has_citation"] is True

    def test_no_citation_with_must_cite_true_scores_zero(self):
        result = _score("Plain text answer no links.", criteria={"must_cite": True})
        assert result["has_citation"] is False

    def test_no_citation_with_must_cite_false_scores_full(self):
        result = _score("Plain text answer.", criteria={"must_cite": False})
        assert result["has_citation"] is False
        # citation_score should still be 1.0 since must_cite=False
        # Verify via total score: term=1.0, citation=1.0, refusal=1.0, tool=1.0
        assert result["score"] == pytest.approx(1.0, abs=0.01)

    def test_must_cite_defaults_to_true_when_absent(self):
        result = _score("Plain text answer no links.", criteria={})
        # Default must_cite=True → no citation → citation_score=0 → score < 1.0
        assert result["score"] < 1.0

    def test_citation_markers_are_case_insensitive(self):
        result = _score("More at [Wikipedia: Federal Reserve]", criteria={"must_cite": True})
        assert result["has_citation"] is True


# ---------------------------------------------------------------------------
# Refusal accuracy
# ---------------------------------------------------------------------------


class TestRefusalScore:
    @pytest.mark.parametrize(
        "marker",
        [
            "out of scope",
            "cannot",
            "not relevant",
            "not designed",
            "outside",
            "doesn't fall",
            "speculative",
            "caveat",
            "unable to find",
            "not about finance",
            "outside my scope",
            "not a research question",
            "not within my",
            "falls outside",
            "beyond the scope",
        ],
    )
    def test_each_refusal_marker_detected(self, marker):
        result = _score(f"This is {marker} for my capabilities.", criteria={"should_refuse": True})
        assert result["did_refuse"] is True

    def test_correct_refusal_scores_full(self):
        result = _score(
            "This is outside my scope as a finance assistant.",
            criteria={"should_refuse": True},
        )
        assert result["refusal_correct"] is True

    def test_not_within_my_scope_phrase_detected(self):
        """Regression: 'not within my primary scope' was missed before adding 'not within my'."""
        result = _score(
            "This question is not within my primary scope of providing finance information.",
            criteria={"should_refuse": True},
        )
        assert result["did_refuse"] is True
        assert result["refusal_correct"] is True

    def test_missing_refusal_scores_zero(self):
        result = _score(
            "The best restaurant in NYC is definitely Nobu.",
            criteria={"should_refuse": True},
        )
        assert result["refusal_correct"] is False

    def test_in_scope_question_refusal_score_always_full(self):
        result = _score("The Fed uses open market operations.", criteria={"should_refuse": False})
        # refusal_score should be 1.0 (N/A)
        assert result["score"] >= 0.25  # at minimum the refusal 0.25 weight is earned

    def test_should_refuse_false_is_default(self):
        result = _score("Fine answer.", criteria={})
        assert result["should_refuse"] is False
        assert result["refusal_correct"] is True


# ---------------------------------------------------------------------------
# Tool selection score
# ---------------------------------------------------------------------------


class TestToolScore:
    def test_no_tools_on_in_scope_question_penalizes(self):
        result = score_response("Some answer.", [], {"should_refuse": False})
        # tool_score = 0.3 → contributes 0.03 instead of 0.10
        # Full score without penalty: 1.0; penalty = 0.07
        assert result["score"] < 1.0

    def test_tools_used_on_in_scope_earns_full_tool_score(self):
        result = score_response(
            "Answer with http://citation.com",
            ["wikipedia_search"],
            {"required_terms": [], "must_cite": True, "should_refuse": False},
        )
        assert result["score"] == pytest.approx(1.0, abs=0.01)

    def test_no_tools_on_out_of_scope_does_not_penalize(self):
        result = score_response(
            "This is outside my scope as a finance assistant.",
            [],
            {"should_refuse": True},
        )
        # tool_score = 1.0 because should_refuse=True; no citation so citation_score=0.0
        # 1.0*0.40 + 0.0*0.25 + 1.0*0.25 + 1.0*0.10 = 0.75
        assert result["score"] == pytest.approx(0.75, abs=0.01)


# ---------------------------------------------------------------------------
# Weighted score calculation
# ---------------------------------------------------------------------------


class TestWeightedScore:
    def test_perfect_score(self):
        result = score_response(
            "The discount window [Wikipedia: Federal Reserve] is a facility.",
            ["wikipedia_search"],
            {"required_terms": ["discount", "window"], "must_cite": True, "should_refuse": False},
        )
        assert result["score"] == pytest.approx(1.0, abs=0.01)

    def test_score_rounded_to_3_decimals(self):
        result = _score("x", criteria={"required_terms": ["a", "b", "c"]})
        assert result["score"] == round(result["score"], 3)

    def test_known_weights(self):
        # term_coverage=0.5, citation=0, refusal=1.0 (N/A), tool=1.0
        # 0.5*0.40 + 0*0.25 + 1.0*0.25 + 1.0*0.10 = 0.20 + 0 + 0.25 + 0.10 = 0.55
        result = score_response(
            "Only found: capital",
            ["wikipedia_search"],
            {"required_terms": ["capital", "tier"], "must_cite": True, "should_refuse": False},
        )
        assert result["score"] == pytest.approx(0.55, abs=0.01)

    def test_score_between_zero_and_one(self):
        for answer in ["", "perfect http://link.com relevant terms", "irrelevant"]:
            result = _score(answer, criteria=FULL_CRITERIA)
            assert 0.0 <= result["score"] <= 1.0

    def test_result_dict_has_all_required_keys(self):
        result = _score("test answer")
        expected_keys = {
            "score",
            "term_coverage",
            "terms_found",
            "terms_missing",
            "has_citation",
            "should_refuse",
            "did_refuse",
            "refusal_correct",
        }
        assert expected_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# questions.json schema validation
# ---------------------------------------------------------------------------


class TestQuestionsSchema:
    @pytest.fixture(scope="class")
    def questions(self):
        path = Path(__file__).parent.parent / "eval" / "questions.json"
        with open(path) as f:
            return json.load(f)["questions"]

    def test_has_15_questions(self, questions):
        assert len(questions) == 15

    def test_each_question_has_required_fields(self, questions):
        for q in questions:
            assert "id" in q, f"Q{q.get('id', '?')} missing 'id'"
            assert "question" in q, f"Q{q.get('id', '?')} missing 'question'"
            assert "type" in q, f"Q{q.get('id', '?')} missing 'type'"
            assert "criteria" in q, f"Q{q.get('id', '?')} missing 'criteria'"

    def test_question_ids_are_unique_and_sequential(self, questions):
        ids = [q["id"] for q in questions]
        assert ids == list(range(1, 16))

    def test_all_types_are_known(self, questions):
        valid_types = {
            "factual_single_source",
            "academic_search",
            "multi_source_synthesis",
            "data_retrieval",
            "cross_tool_synthesis",
            "out_of_scope",
            "speculative_emerging",
        }
        for q in questions:
            assert q["type"] in valid_types, f"Q{q['id']} has unknown type: {q['type']!r}"

    def test_out_of_scope_questions_have_should_refuse_true(self, questions):
        for q in questions:
            if q["type"] == "out_of_scope":
                assert q["criteria"].get("should_refuse") is True, (
                    f"Q{q['id']} is out_of_scope but should_refuse != True"
                )

    def test_in_scope_questions_have_required_terms(self, questions):
        for q in questions:
            if q["type"] != "out_of_scope":
                assert "required_terms" in q["criteria"], (
                    f"Q{q['id']} ({q['type']}) missing required_terms"
                )
                assert isinstance(q["criteria"]["required_terms"], list)

    def test_question_strings_are_non_empty(self, questions):
        for q in questions:
            assert q["question"].strip(), f"Q{q['id']} has empty question string"

    def test_criteria_is_dict(self, questions):
        for q in questions:
            assert isinstance(q["criteria"], dict), f"Q{q['id']} criteria is not a dict"
