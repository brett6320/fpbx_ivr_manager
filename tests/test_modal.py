"""Destructive actions are gated by the shared confirmation modal (not native
confirm()), and the layout is full-width."""
from fastapi.templating import Jinja2Templates

_T = Jinja2Templates(directory="app/web/templates").env


def test_base_has_modal_and_is_full_width():
    html = _T.get_template("base.html").render(request=None)
    assert 'id="confirmModal"' in html and 'class="modal-overlay"' in html
    assert "js-confirm" in html          # the intercept hook
    assert "max-width: none" in html     # content/nav fill the window
    assert "onsubmit" not in html


def test_schedule_delete_uses_modal_not_native_confirm():
    c = {"extension": 9550, "label": "TC", "open_destination": "2000",
         "enabled": True, "in_pool": True, "closures": []}
    html = _T.get_template("schedules.html").render(
        request=None, user={"name": "A"}, can_admin=True, schedules=[c])
    assert 'class="js-confirm"' in html
    assert "data-confirm=" in html
    assert "return confirm(" not in html          # no single-click / native confirm
    assert 'name="confirm" value="yes"' in html   # server-side token still present


def test_all_destructive_templates_dropped_native_confirm():
    rows = {
        "ivrs.html": {"ivrs": [{"extension": 9560, "name": "Main", "enabled": True}], "recycled": []},
        "users.html": {"is_local": True,
                        "users": [{"username": "a", "display_name": "A", "is_admin": False,
                                   "has_mfa": True, "groups": []}]},
        "phrases.html": {"phrases": [{"name": "ivrmgr_p", "filename": "p.wav",
                                      "description": "d", "generated": True}], "can_admin": True},
    }
    for tpl, ctx in rows.items():
        html = _T.get_template(tpl).render(request=None, user={"name": "A"}, **ctx)
        assert "return confirm(" not in html, tpl
        assert 'class="js-confirm"' in html, tpl
