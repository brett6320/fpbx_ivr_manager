"""Guard the systemd installer: valid bash, executable, and the unit templates
cleanly to a chosen install dir / service group."""
import os
import re
import shutil
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "deploy", "install.sh")
UNIT = os.path.join(REPO, "deploy", "fpbx-ivr-manager.service")
TOP = os.path.join(REPO, "install.sh")


def test_top_installer_recommends_and_gates_before_acting():
    assert os.path.exists(TOP) and os.access(TOP, os.X_OK)
    subprocess.run([shutil.which("bash"), "-n", TOP], check=True)
    body = open(TOP).read()
    # probes both variants, recommends, and dispatches to each sub-installer
    assert "Recommendation:" in body
    assert "deploy/install.sh" in body and "fusionpbx-app/install.sh" in body
    # verifies compatibility for the CHOSEN variant BEFORE acting (a gate)
    assert "Refusing:" in body
    assert "--check" in body                 # can report without acting


def test_install_script_is_executable_and_valid_bash():
    assert os.path.exists(SCRIPT)
    assert os.access(SCRIPT, os.X_OK), "install.sh must be executable"
    bash = shutil.which("bash")
    assert bash, "bash required for this test"
    subprocess.run([bash, "-n", SCRIPT], check=True)  # syntax check


def test_install_script_prompts_and_defaults():
    body = open(SCRIPT).read()
    assert "/opt/ivr-manager" in body               # default install location offered
    assert "Start the service automatically at boot" in body
    assert "systemctl enable" in body and "systemctl disable" in body  # boot toggle
    assert "useradd --system" in body               # unprivileged service user


def test_install_script_enforces_min_python_and_sanitizes_extras():
    body = open(SCRIPT).read()
    # picks/validates a Python >= the app minimum, with an override hook
    assert "pick_python" in body and "MIN_PY_MINOR" in body
    assert "PYTHON" in body                          # honors a $PYTHON override
    assert 'tr -d ' in body and "extras" in body     # strips whitespace from extras


def test_install_script_min_python_matches_pyproject():
    import re
    proj = open(os.path.join(REPO, "pyproject.toml")).read()
    m = re.search(r'requires-python\s*=\s*">=(\d+)\.(\d+)"', proj)
    assert m, "requires-python not found in pyproject.toml"
    major, minor = m.group(1), m.group(2)
    body = open(SCRIPT).read()
    assert f"MIN_PY_MAJOR={major}" in body and f"MIN_PY_MINOR={minor}" in body


def test_unit_uses_templating_token():
    unit = open(UNIT).read()
    # the installer rewrites this canonical path to the chosen INSTALL_DIR
    assert "WorkingDirectory=/opt/ivr-manager" in unit
    assert "ExecStart=/opt/ivr-manager/.venv/bin/uvicorn" in unit


def test_templating_replaces_path_and_can_strip_freeswitch_rwp():
    unit = open(UNIT).read()
    install_dir = "/srv/ivr"
    # emulate the installer's sed: path + group substitution
    out = unit.replace("/opt/ivr-manager", install_dir)
    out = re.sub(r"^Group=freeswitch$", "Group=ivrmgr", out, flags=re.M)
    # and, for a non-freeswitch group, dropping the recordings RW path
    out = "\n".join(ln for ln in out.splitlines()
                    if "ReadWritePaths=/var/lib/freeswitch/storage" not in ln)
    assert f"WorkingDirectory={install_dir}" in out
    assert f"ExecStart={install_dir}/.venv/bin/uvicorn" in out
    assert "Group=ivrmgr" in out
    assert "ReadWritePaths=/var/lib/freeswitch/storage" not in out
    assert "/opt/ivr-manager" not in out            # no stale default paths remain
