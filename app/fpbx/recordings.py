"""Manage FusionPBX recordings (v_recordings) for schedule greetings.

Storage modes (settings.fpbx_recording_storage):
  - 'local': write the .wav directly into the FS recordings dir (app runs ON the
             PBX host). Default.
  - 'db'   : store base64 of the .wav in v_recordings.recording_base64
  - 'sftp' : push the .wav to a remote FS host (only if not co-located)
"""
from __future__ import annotations

import base64
import io
import os
import posixpath
import uuid

from app.config import settings
from app.fpbx.db import cursor, domain_uuid
from app.fpbx.time_conditions import MARKER, NotManaged

# Tag stored in recording_description so we can prove ownership before overwrite.
_REC_TAG = f"[{MARKER}]"


def _fetch_recording(cur, d: str, name: str) -> dict | None:
    cur.execute(
        "SELECT recording_uuid, recording_description FROM v_recordings "
        "WHERE domain_uuid = %s AND recording_name = %s",
        (d, name),
    )
    return cur.fetchone()


def _local_put(filename: str, data: bytes) -> None:
    os.makedirs(settings.recordings_dir, exist_ok=True)
    path = os.path.join(settings.recordings_dir, filename)
    with open(path, "wb") as f:
        f.write(data)
    # group-readable (FreeSWITCH runs in the shared group), not world-readable
    os.chmod(path, 0o640)


def _sftp_put(filename: str, data: bytes) -> None:
    import paramiko  # optional dep, only for non-co-located deployments

    key = paramiko.Ed25519Key.from_private_key_file(settings.fs_ssh_key_path)
    client = paramiko.SSHClient()
    # Validate the host key against known_hosts; reject unknown hosts (no MITM).
    # The operator must add the FreeSWITCH host to known_hosts before first use.
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        settings.fs_ssh_host,
        port=settings.fs_ssh_port,
        username=settings.fs_ssh_user,
        pkey=key,
    )
    try:
        sftp = client.open_sftp()
        remote = posixpath.join(settings.fs_recordings_dir, filename)
        _sftp_makedirs(sftp, settings.fs_recordings_dir)
        sftp.putfo(io.BytesIO(data), remote)
        sftp.chmod(remote, 0o640)
    finally:
        client.close()


def _sftp_makedirs(sftp, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    path = ""
    for p in parts:
        path += "/" + p
        try:
            sftp.stat(path)
        except FileNotFoundError:
            sftp.mkdir(path)


def list_recordings() -> list[dict]:
    """Existing recordings in the domain, for the IVR 'existing recording' choice."""
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT recording_name, recording_filename FROM v_recordings "
            "WHERE domain_uuid = %s AND recording_filename IS NOT NULL "
            "ORDER BY recording_name",
            (d,),
        )
        return [
            {"name": r["recording_name"], "filename": r["recording_filename"]}
            for r in cur.fetchall()
        ]


def upsert_recording(name: str, wav: bytes, description: str = "") -> str:
    """Create/replace a recording. Returns recording_filename to use in dialplan.

    Refuses to overwrite a recording that lacks our ownership tag, so we never
    clobber a prompt a human uploaded — even one that happens to share our name.
    """
    d = domain_uuid()
    filename = f"{name}.wav"
    tagged_desc = f"{description} {_REC_TAG}".strip()

    # Guardrail: verify ownership before we touch the filesystem or DB.
    with cursor() as cur:
        existing = _fetch_recording(cur, d, name)
    if existing and _REC_TAG not in (existing["recording_description"] or ""):
        raise NotManaged(
            f"recording {name!r} exists but was not created by this app; refusing to overwrite"
        )

    storage = settings.fpbx_recording_storage
    if storage == "local":
        _local_put(filename, wav)
        b64 = None
    elif storage == "sftp":
        _sftp_put(filename, wav)
        b64 = None
    elif storage == "db":
        b64 = base64.b64encode(wav).decode()
    else:
        raise ValueError(f"unknown FPBX_RECORDING_STORAGE={storage!r}")

    with cursor() as cur:
        if existing:
            cur.execute(
                "UPDATE v_recordings SET recording_filename=%s, recording_base64=%s, "
                "recording_description=%s WHERE recording_uuid=%s",
                (filename, b64, tagged_desc, existing["recording_uuid"]),
            )
        else:
            cur.execute(
                "INSERT INTO v_recordings "
                "(recording_uuid, domain_uuid, recording_name, recording_filename, "
                " recording_base64, recording_description) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), d, name, filename, b64, tagged_desc),
            )
    return filename
