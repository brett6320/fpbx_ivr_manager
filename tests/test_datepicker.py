"""The start/end datetime pickers keep the end at or after the start."""
from fastapi.templating import Jinja2Templates

_T = Jinja2Templates(directory="app/web/templates")


def _has_sync(html: str) -> bool:
    # end.min pinned to start, and end pre-selected to start when blank/behind
    return "end.min = start.value" in html and "end.value = start.value" in html


def test_schedule_form_syncs_end_to_start():
    html = _T.env.get_template("schedule_form.html").render(
        request=None, user={"name": "A"}, c=None, adopt_uuid=None, org="Acme",
        pool="9550-9599", placeholders=[])
    assert _has_sync(html)


def test_flow_form_syncs_end_to_start():
    html = _T.env.get_template("flow_form.html").render(
        request=None, user={"name": "A"}, pool="9550-9599", digits=["0"],
        destinations=[], recordings=[], placeholders=[], inbound_destinations=[])
    assert _has_sync(html)
