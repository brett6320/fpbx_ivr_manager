from datetime import datetime

from app.fpbx.time_conditions import (
    TIME_CONDITIONS_APP_UUID,
    Closure,
    _is_managed,
    build_dialplan_xml,
    parse_closures,
    sort_closures,
)


def _cl(label, start, end, action="voicemail", rec="g.wav", reason="", reopen=""):
    return Closure(label=label, start=start, end=end, closed_action=action,
                   recording_filename=rec, reason=reason, reopen=reopen)


def test_time_conditions_app_uuid_is_the_fusionpbx_constant():
    assert TIME_CONDITIONS_APP_UUID == "4b821450-926b-175a-af93-a03c441818b1"


def test_single_closure_roundtrip_voicemail():
    start, end = datetime(2026, 7, 7, 0, 0, 0), datetime(2026, 7, 9, 23, 59, 0)
    xml = build_dialplan_xml(
        9550, [_cl("Summer", start, end, rec="schedule_9550_july.wav", reason="a holiday")],
        open_destination="2000", domain="pbx.test",
    )
    p = parse_closures(xml)
    assert p["open_destination"] == "2000"
    assert len(p["closures"]) == 1
    c = p["closures"][0]
    assert c["start"] == start and c["end"] == end
    assert c["closed_action"] == "voicemail"
    assert c["label"] == "Summer" and c["reason"] == "a holiday"
    assert c["recording_filename"] == "schedule_9550_july.wav"


def test_reopen_roundtrips_in_closure_metadata():
    start, end = datetime(2026, 7, 3, 0, 0, 0), datetime(2026, 7, 3, 23, 59, 0)
    xml = build_dialplan_xml(
        9551, [_cl("Independence", start, end, reopen="2026-07-06 09:00:00")],
        open_destination="2000", domain="pbx.test",
    )
    p = parse_closures(xml)
    assert p["closures"][0]["reopen"] == "2026-07-06 09:00:00"


def test_missing_reopen_parses_empty():
    start, end = datetime(2026, 7, 3, 0, 0, 0), datetime(2026, 7, 3, 23, 59, 0)
    xml = build_dialplan_xml(9552, [_cl("Plain", start, end)],
                             open_destination="2000", domain="pbx.test")
    assert parse_closures(xml)["closures"][0]["reopen"] == ""


def test_hangup_roundtrip():
    xml = build_dialplan_xml(
        9599, [_cl("NY", datetime(2026, 1, 1, 9, 0, 0), datetime(2026, 1, 1, 17, 0, 0),
                   action="hangup")],
        open_destination="ivr_main", domain="pbx.test",
    )
    p = parse_closures(xml)
    assert p["closures"][0]["closed_action"] == "hangup"
    assert p["open_destination"] == "ivr_main"


def test_multiple_closures_most_specific_first():
    # a one-day closure and a week-long closure in one TC
    jul4 = _cl("July 4th", datetime(2026, 7, 4, 0, 0), datetime(2026, 7, 4, 23, 59))
    week = _cl("Summer break", datetime(2026, 7, 1, 0, 0), datetime(2026, 7, 8, 23, 59))
    xml = build_dialplan_xml(9550, [week, jul4], open_destination="2000", domain="pbx.test")
    p = parse_closures(xml)
    labels = [c["label"] for c in p["closures"]]
    # shortest (most specific) window listed first
    assert labels == ["July 4th", "Summer break"]
    # each closure breaks on a match so a live one stops evaluation
    assert xml.count('break="on-true"') == 2


def test_sort_closures_orders_by_duration_then_start():
    a = _cl("a", datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 12, 0))   # 12h
    b = _cl("b", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 3, 0, 0))    # 2d
    c = _cl("c", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 12, 0))   # 12h earlier
    assert [x.label for x in sort_closures([a, b, c])] == ["c", "a", "b"]


def test_generated_xml_is_owned():
    xml = build_dialplan_xml(
        9550, [_cl("x", datetime(2026, 7, 7, 0, 0), datetime(2026, 7, 7, 23, 59))],
        open_destination="2000", domain="pbx.test",
    )
    assert _is_managed(xml) is True


def test_foreign_xml_not_owned():
    foreign = '<extension name="schedule_9550"><condition/></extension>'
    assert _is_managed(foreign) is False
    assert _is_managed(None) is False
    assert _is_managed("") is False


def test_playback_and_dest_match_present():
    xml = build_dialplan_xml(
        9551, [_cl("x", datetime(2026, 3, 1, 0, 0), datetime(2026, 3, 1, 23, 59))],
        open_destination="2000", domain="pbx.test",
    )
    assert 'destination_number" expression="^9551$"' in xml
    assert 'application="playback"' in xml
    # trailing open condition transfers to the daytime destination
    assert 'transfer" data="2000 XML pbx.test"' in xml


def test_parse_handles_legacy_single_condition_format():
    # older format: one date-time condition + anti-action transfer, no metadata comment
    legacy = (
        '<!-- fpbx-ivr-manager:managed -->\n'
        '<extension name="schedule_9550" continue="false">\n'
        '  <condition field="destination_number" expression="^9550$" break="on-false"/>\n'
        '  <condition date-time="2026-07-07 00:00:00~2026-07-07 23:59:00">\n'
        '    <action application="playback" data="$${recordings}/old.wav"/>\n'
        '    <action application="voicemail" data="default pbx.test 9550"/>\n'
        '    <anti-action application="transfer" data="2000 XML pbx.test"/>\n'
        '  </condition>\n'
        '</extension>'
    )
    p = parse_closures(legacy)
    assert p["open_destination"] == "2000"
    assert len(p["closures"]) == 1
    assert p["closures"][0]["closed_action"] == "voicemail"
    assert p["closures"][0]["recording_filename"] == "old.wav"
