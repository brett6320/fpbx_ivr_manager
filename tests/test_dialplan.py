from datetime import datetime

from app.fpbx.time_conditions import (
    TIME_CONDITIONS_APP_UUID,
    _is_managed,
    _parse_xml,
    build_dialplan_xml,
)


def test_time_conditions_app_uuid_is_the_fusionpbx_constant():
    # FusionPBX's fixed Time Conditions app_uuid — changing it would stop our
    # records from appearing as native time conditions.
    assert TIME_CONDITIONS_APP_UUID == "4b821450-926b-175a-af93-a03c441818b1"


def test_xml_roundtrip_voicemail():
    start = datetime(2026, 7, 7, 0, 0, 0)
    end = datetime(2026, 7, 9, 23, 59, 0)
    xml = build_dialplan_xml(
        9550, (start, end), "schedule_9550_july.wav",
        closed_action="voicemail", open_destination="2000", domain="pbx.test",
    )
    p = _parse_xml(xml)
    assert p["start"] == start
    assert p["end"] == end
    assert p["open_destination"] == "2000"
    assert p["closed_action"] == "voicemail"


def test_xml_roundtrip_hangup():
    xml = build_dialplan_xml(
        9599, (datetime(2026, 1, 1, 9, 0, 0), datetime(2026, 1, 1, 17, 0, 0)),
        "x.wav", closed_action="hangup", open_destination="ivr_main", domain="pbx.test",
    )
    p = _parse_xml(xml)
    assert p["closed_action"] == "hangup"
    assert p["open_destination"] == "ivr_main"


def test_generated_xml_is_owned():
    xml = build_dialplan_xml(
        9550, (datetime(2026, 7, 7, 0, 0, 0), datetime(2026, 7, 7, 23, 59, 0)),
        "g.wav", closed_action="hangup", open_destination="2000", domain="pbx.test",
    )
    assert _is_managed(xml) is True


def test_foreign_xml_not_owned():
    foreign = '<extension name="schedule_9550"><condition/></extension>'
    assert _is_managed(foreign) is False
    assert _is_managed(None) is False
    assert _is_managed("") is False


def test_playback_and_dest_match_present():
    xml = build_dialplan_xml(
        9551, (datetime(2026, 3, 1, 0, 0, 0), datetime(2026, 3, 1, 23, 59, 0)),
        "g.wav", closed_action="voicemail", open_destination="2000", domain="pbx.test",
    )
    assert 'destination_number" expression="^9551$"' in xml
    assert 'application="playback"' in xml
