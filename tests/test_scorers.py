"""Tests for the eval scorers (pure parts: numeric extraction/tolerance, entity
matching, and the scorer dispatch). The LLM judge is not unit-tested here."""
from eval import scorers


# --- extract_numbers --------------------------------------------------------
def test_extract_numbers_handles_scale_words_and_currency():
    vals = scorers.extract_numbers("Revenue was $215.938 billion, up from $130.5 billion.")
    assert 215_938_000_000 in vals
    assert 130_500_000_000 in vals


def test_extract_numbers_treats_percent_as_plain_number():
    assert 49.5 in scorers.extract_numbers("Gross margin was 49.5% this year.")


def test_extract_numbers_handles_negatives_and_millions():
    vals = scorers.extract_numbers("A net loss of -$18,756 million.")
    assert -18_756_000_000 in vals


# --- numeric_ok -------------------------------------------------------------
def test_numeric_ok_within_tolerance():
    # "$215.9B" is within 2% of the exact gold figure.
    assert scorers.numeric_ok("about $215.9 billion", 215_938_000_000, 0.02)


def test_numeric_ok_rejects_out_of_tolerance():
    assert not scorers.numeric_ok("about $190 billion", 215_938_000_000, 0.02)


def test_numeric_ok_passes_if_any_number_matches():
    # Answer mentions a year and the figure; the figure should satisfy the check.
    assert scorers.numeric_ok("In FY2026 revenue reached $215.94 billion.", 215_938_000_000)


def test_numeric_ok_percentage_value():
    assert scorers.numeric_ok("Gross margin was roughly 49.5%.", 49.52, 0.02)


def test_numeric_ok_loss_phrased_as_positive_magnitude():
    # "net loss of $18.756 billion" should match a negative gold figure.
    assert scorers.numeric_ok("Intel reported a net loss of $18.756 billion.", -18_756_000_000)


def test_numeric_ok_rejects_profit_claim_against_loss_gold():
    # Same magnitude but framed as income (no loss signal) must NOT pass.
    assert not scorers.numeric_ok("Intel had net income of $18.756 billion.", -18_756_000_000)


def test_numeric_ok_unicode_minus():
    assert scorers.numeric_ok("Net income was −$18.756 billion.", -18_756_000_000)


# --- entity_ok --------------------------------------------------------------
def test_entity_ok_requires_all_entities_case_insensitive():
    answer = "AMD competes with intel in CPUs and nvidia in GPUs."
    assert scorers.entity_ok(answer, ["Intel", "NVIDIA"])


def test_entity_ok_fails_when_one_missing():
    assert not scorers.entity_ok("AMD competes mainly with Intel.", ["Intel", "NVIDIA"])


# --- score() dispatch -------------------------------------------------------
def test_score_numeric_pass_and_fail():
    item = {"scorer": "numeric", "gold_numeric": 100.0}
    assert scorers.score(item, "the value is 101")[0] == "PASS"
    assert scorers.score(item, "the value is 50")[0] == "FAIL"


def test_score_entity_dispatch():
    item = {"scorer": "entity", "gold_entities": ["Intel"]}
    assert scorers.score(item, "it names Intel")[0] == "PASS"
    assert scorers.score(item, "no companies here")[0] == "FAIL"


def test_score_judge_disabled_is_unscored():
    item = {"scorer": "judge", "question": "q", "gold_answer": "a"}
    verdict, _ = scorers.score(item, "some answer", enable_judge=False)
    assert verdict == "UNSCORED"


def test_score_unknown_scorer():
    assert scorers.score({"scorer": "mystery"}, "x")[0] == "UNSCORED"
