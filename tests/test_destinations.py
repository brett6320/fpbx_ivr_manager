"""Schedules can fall through to a time condition, and the business config
carries default destinations — parity with the FusionPBX PHP app."""
from fastapi.templating import Jinja2Templates

from app.web import routes

_T = Jinja2Templates(directory="app/web/templates").env


def test_resolve_dest_select_vs_manual():
    assert routes._resolve_dest({"d": "2000"}, "d") == "2000"
    assert routes._resolve_dest({"d": "__manual__", "d_manual": "5551212"}, "d") == "5551212"
    assert routes._resolve_dest({"d": "", "d_manual": "9"}, "d") == "9"
    assert routes._resolve_dest({}, "d") == ""


def _dest(kind, number, label):
    return {"kind": kind, "number": number, "label": label, "value": f"transfer {number} XML d"}


def test_schedule_form_open_destination_lists_time_conditions():
    dests = [_dest("time_condition", "5001", "Time condition 5001 — Office Hours")]
    html = _T.get_template("schedule_form.html").render(
        request=None, user={"name": "A"}, c=None, adopt_uuid=None, org="Acme",
        pool="9550-9599", placeholders=[], destinations=dests, default_open="5001")
    assert "Time condition 5001 — Office Hours" in html
    assert 'name="open_destination"' in html
    assert 'value="5001" selected' in html          # new TC defaults to on-hours dest
    assert 'name="open_destination_manual"' in html  # manual escape hatch present


def test_business_form_has_default_destinations():
    dests = [_dest("ring_group", "3000", "Ring group 3000 — Sales")]
    html = _T.get_template("business.html").render(
        request=None, user={"name": "A"}, business_name="", org_fallback="Acme", rows=[],
        closure_opening="", closure_closing="", default_opening="o", default_closing="c",
        placeholders=[], destinations=dests,
        dests={"on_hours": "3000", "off_hours": "", "emergency": ""}, saved=False)
    assert "Default destinations" in html
    assert 'name="dest_on_hours"' in html and 'name="dest_off_hours"' in html and 'name="dest_emergency"' in html
    assert 'value="3000" selected' in html
