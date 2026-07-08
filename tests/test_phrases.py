from datetime import datetime

import pytest

from app.phrases.builder import build_phrase, describe_window


def d(y, m, day, hh=0, mm=0):
    return datetime(y, m, day, hh, mm)


def test_full_single_day():
    s = describe_window(d(2026, 7, 7, 0, 0), d(2026, 7, 7, 23, 59))
    assert s == "We are closed all day Tuesday, July 7th."


def test_multi_full_days():
    s = describe_window(d(2026, 7, 7, 0, 0), d(2026, 7, 9, 23, 59))
    assert "from Tuesday, July 7th through Thursday, July 9th" in s


def test_early_close():
    s = describe_window(d(2026, 7, 7, 9, 0), d(2026, 7, 7, 23, 59))
    assert s == "We are closing early at 9:00 AM on Tuesday, July 7th."


def test_late_open():
    s = describe_window(d(2026, 7, 7, 0, 0), d(2026, 7, 7, 12, 0))
    assert s == "We are opening late at 12:00 PM on Tuesday, July 7th."


def test_partial_same_day():
    s = describe_window(d(2026, 7, 7, 12, 0), d(2026, 7, 7, 15, 0))
    assert s == "We are closed from 12:00 PM to 3:00 PM on Tuesday, July 7th."


def test_partial_across_days():
    s = describe_window(d(2026, 7, 7, 15, 0), d(2026, 7, 8, 9, 0))
    assert "from Tuesday, July 7th at 3:00 PM until Wednesday, July 8th at 9:00 AM" in s


def test_end_before_start_raises():
    with pytest.raises(ValueError):
        describe_window(d(2026, 7, 7, 12, 0), d(2026, 7, 7, 9, 0))


def test_build_phrase_assembles_three_parts():
    p = build_phrase(d(2026, 7, 7, 0, 0), d(2026, 7, 7, 23, 59), org_name="Acme", reason="a holiday")
    assert p.text.startswith("Thank you for calling Acme.")
    assert "closed all day Tuesday, July 7th" in p.text
    assert "This closure is for a holiday." in p.text
    assert p.text.rstrip().endswith("Goodbye.")


def test_build_phrase_honors_custom_opening_and_closing():
    p = build_phrase(
        d(2026, 7, 7, 0, 0), d(2026, 7, 7, 23, 59), org_name="Acme",
        opening="Hello, you have reached us.", closing="So long.",
    )
    assert p.opening == "Hello, you have reached us."
    assert p.closing == "So long."
    # the varying body is still inserted between them
    assert "closed all day Tuesday, July 7th" in p.text
    assert p.text.rstrip().endswith("So long.")
