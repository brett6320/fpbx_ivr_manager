"""Guard the systemd installer: valid bash, executable, and the unit templates
cleanly to a chosen install dir / service group."""
import os
import re
import shutil
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "deploy", "install.sh")
UNIT = os.path.join(REPO, "deploy", "fpbx-ivr-manager.service")


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
