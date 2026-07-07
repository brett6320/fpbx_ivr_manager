#!/usr/bin/env python3
"""Local user management CLI (only relevant when AUTH_BACKEND=local).

Permissions are granted to groups (see AUTHZ_GROUP_PERMISSIONS), never to a user
directly. Use group-add/group-remove to place users in groups.

Usage:
  python manage.py add <username> [--name "Full Name"]   # prompts for password
  python manage.py passwd <username>                      # reset password
  python manage.py delete <username>
  python manage.py list
  python manage.py group-add <username> <group>
  python manage.py group-remove <username> <group>
  python manage.py groups <username>
  python manage.py set-admin <username> [--off]   # local admin flag (MFA required)
  python manage.py mfa-reset <username>           # clear TOTP + passkeys (recovery)
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app.auth import local


def main() -> int:
    p = argparse.ArgumentParser(prog="manage.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="create a user")
    a.add_argument("username")
    a.add_argument("--name", default=None)
    a.add_argument("--admin", action="store_true", help="mark as local admin (MFA required)")

    sa = sub.add_parser("set-admin", help="toggle the local admin flag")
    sa.add_argument("username")
    sa.add_argument("--off", action="store_true", help="remove admin instead of granting")

    mr = sub.add_parser("mfa-reset", help="clear a user's MFA factors")
    mr.add_argument("username")

    pw = sub.add_parser("passwd", help="reset a user's password")
    pw.add_argument("username")

    d = sub.add_parser("delete", help="delete a user")
    d.add_argument("username")

    sub.add_parser("list", help="list usernames")

    ga = sub.add_parser("group-add", help="add a user to a group")
    ga.add_argument("username")
    ga.add_argument("group")

    gr = sub.add_parser("group-remove", help="remove a user from a group")
    gr.add_argument("username")
    gr.add_argument("group")

    gl = sub.add_parser("groups", help="list a user's groups")
    gl.add_argument("username")

    args = p.parse_args()

    if args.cmd == "group-add":
        local.add_to_group(args.username, args.group)
        print(f"{args.username} added to {args.group}")
        return 0
    if args.cmd == "group-remove":
        ok = local.remove_from_group(args.username, args.group)
        print("removed" if ok else "was not a member")
        return 0
    if args.cmd == "groups":
        for g in local.groups_for_user(args.username):
            print(g)
        return 0
    if args.cmd == "set-admin":
        ok = local.set_admin(args.username, not args.off)
        print("updated" if ok else "no such user")
        return 0
    if args.cmd == "mfa-reset":
        local.reset_mfa(args.username)
        print(f"MFA cleared for {args.username}")
        return 0

    if args.cmd == "list":
        for u in local.list_users():
            print(u)
        return 0
    if args.cmd == "delete":
        print("deleted" if local.delete_user(args.username) else "no such user")
        return 0
    if args.cmd == "passwd":
        pwd = _read_password()
        if pwd is None:
            return 1
        print("updated" if local.set_password(args.username, pwd) else "no such user")
        return 0
    if args.cmd == "add":
        pwd = _read_password()
        if pwd is None:
            return 1
        local.create_user(args.username, pwd, args.name, is_admin=args.admin)
        print(f"saved {args.username}" + (" (admin, MFA required)" if args.admin else ""))
        return 0
    return 1


def _read_password() -> str | None:
    pwd = getpass.getpass("Password: ")
    if pwd != getpass.getpass("Confirm: "):
        print("passwords do not match", file=sys.stderr)
        return None
    return pwd


if __name__ == "__main__":
    raise SystemExit(main())
