from fastapi.templating import Jinja2Templates

_T = Jinja2Templates(directory="app/web/templates")


def _render_login() -> str:
    return _T.env.get_template("login.html").render(
        request=None, org="Acme", sso=False, error=None, show_local_admin=False
    )


def test_base_supports_system_and_manual_theme():
    html = _render_login()
    # follows the system theme by default
    assert "color-scheme: light dark" in html
    assert "light-dark(" in html
    # manual override toggle (auto/light/dark) persisted in the browser
    assert 'id="themeSel"' in html
    assert "localStorage" in html
    for opt in ("auto", "light", "dark"):
        assert f'value="{opt}"' in html


def test_templates_use_theme_variables_not_hardcoded_light_colors():
    html = _T.env.get_template("schedules.html").render(
        request=None, user={"name": "A"}, schedules=[], can_admin=True
    )
    # the table borders use the theme variable (base's light-dark palette still
    # names the hex once, so only assert the swept structural style is present)
    assert "1px solid var(--border)" in html
    assert "1px solid #d1d5db" not in html
