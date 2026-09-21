import json

from src.core import confirmations


def test_clean_approve_phrases():
    for text in ("yes", "Yes", "OK", "okay", "go ahead", "да", "ок", "давай"):
        assert confirmations.parse_confirmation(text) == "approve"


def test_clean_reject_phrases():
    for text in ("no", "No", "stop", "нет", "отмена"):
        assert confirmations.parse_confirmation(text) == "reject"


def test_approve_with_extra_words_is_unclear():
    assert confirmations.parse_confirmation("да, но сначала логи") == "unclear"
    assert confirmations.parse_confirmation("yes but wait for me") == "unclear"


def test_empty_and_whitespace_are_unclear():
    assert confirmations.parse_confirmation("") == "unclear"
    assert confirmations.parse_confirmation("   ") == "unclear"
    assert confirmations.parse_confirmation(None) == "unclear"


def test_punctuation_and_case_are_normalized():
    assert confirmations.parse_confirmation("  Yes!  ") == "approve"
    assert confirmations.parse_confirmation("OK.") == "approve"
    assert confirmations.parse_confirmation("Nope?") == "reject"


def test_more_than_three_words_is_unclear():
    assert confirmations.parse_confirmation("yes please do it now") == "unclear"


def test_unknown_short_phrase_is_unclear():
    assert confirmations.parse_confirmation("banana") == "unclear"
    assert confirmations.parse_confirmation("do the thing") == "unclear"


def test_missing_config_is_fail_safe(tmp_path):
    missing = tmp_path / "nope.json"
    approve, reject = confirmations.load_phrases(missing)
    assert approve == frozenset()
    assert reject == frozenset()
    assert confirmations.parse_confirmation("yes", missing) == "unclear"


def test_corrupt_config_is_fail_safe(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    approve, reject = confirmations.load_phrases(bad)
    assert approve == frozenset()
    assert reject == frozenset()


def test_override_path_is_loaded(tmp_path):
    custom = tmp_path / "custom.json"
    custom.write_text(
        json.dumps({"approve": ["si", "adelante"], "reject": ["para"]}),
        encoding="utf-8",
    )
    assert confirmations.parse_confirmation("si", custom) == "approve"
    assert confirmations.parse_confirmation("adelante", custom) == "approve"
    assert confirmations.parse_confirmation("para", custom) == "reject"
    assert confirmations.parse_confirmation("yes", custom) == "unclear"


def test_shipped_config_lists_are_non_empty():
    approve, reject = confirmations.load_phrases()
    assert "yes" in approve
    assert "да" in approve
    assert "no" in reject
    assert "нет" in reject


def test_long_phrases_are_dropped_with_a_warning(tmp_path, caplog):
    config = tmp_path / "long.json"
    config.write_text(
        json.dumps({"approve": ["go ahead now please", "yes"], "reject": ["no"]}),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        approve, reject = confirmations.load_phrases(config)

    assert "yes" in approve
    assert "go ahead now please" not in approve
    assert "no" in reject
    assert any("go ahead now please" in record.message for record in caplog.records)


def test_missing_config_is_not_cached(tmp_path):
    path = tmp_path / "appears-later.json"
    assert confirmations.load_phrases(path) == (frozenset(), frozenset())

    path.write_text(json.dumps({"approve": ["yes"]}), encoding="utf-8")

    approve, reject = confirmations.load_phrases(path)
    assert "yes" in approve
    assert reject == frozenset()


def test_clear_phrase_cache_rereads_edited_file(tmp_path):
    config = tmp_path / "edit.json"
    config.write_text(json.dumps({"approve": ["yes"]}), encoding="utf-8")
    assert "yes" in confirmations.load_phrases(config)[0]

    config.write_text(json.dumps({"approve": ["yep"]}), encoding="utf-8")
    assert "yes" in confirmations.load_phrases(config)[0]
    assert "yep" not in confirmations.load_phrases(config)[0]

    confirmations.clear_phrase_cache()

    approve, _ = confirmations.load_phrases(config)
    assert "yep" in approve
    assert "yes" not in approve
