# -*- coding: utf-8 -*-
"""Persistent one-time age identity stored outside Kodi addon data."""
from __future__ import annotations

import datetime
import json
import os
import sys


PROFILE_VERSION = 1
PROFILE_ENV = "OTAKU_PRIME_SYSTEM_PROFILE"


class SystemAgeProfileError(RuntimeError):
    pass


class BirthDateLockedError(ValueError):
    pass


def default_system_age_profile_path():
    """Return an OS-user profile path that survives Kodi addon removal.

    Linux follows XDG_CONFIG_HOME and therefore defaults to
    ~/.config/otaku-prime/identity.json. Windows uses LOCALAPPDATA/APPDATA and
    macOS uses the user's Application Support directory. An explicit environment
    override exists for packaging/tests and portable installations.
    """
    override = str(os.environ.get(PROFILE_ENV) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))

    home = os.path.abspath(os.path.expanduser("~"))
    if os.name == "nt":
        root = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.join(home, "AppData", "Local")
        )
        return os.path.join(root, "OtakuPrime", "identity.json")
    if sys.platform == "darwin":
        return os.path.join(
            home, "Library", "Application Support", "OtakuPrime", "identity.json"
        )

    root = str(os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if not root:
        root = os.path.join(home, ".config")
    return os.path.join(os.path.abspath(os.path.expanduser(root)), "otaku-prime", "identity.json")


def _validated_iso_date(value):
    text = str(value or "").strip()
    if not text:
        raise SystemAgeProfileError("system age profile has no birth date")
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise SystemAgeProfileError("system age profile birth date is invalid") from exc


class SystemAgeProfile:
    """Read or create the OS-user DOB file exactly once.

    Prime never provides an update/delete operation for this file. The exclusive
    create also protects concurrent first-save requests. The operating-system
    account owner/root can still alter files outside Prime; application-level
    immutability cannot override OS ownership.
    """

    def __init__(self, path=None):
        self.path = os.path.abspath(os.path.expanduser(
            str(path or default_system_age_profile_path())
        ))

    def read(self):
        if not os.path.exists(self.path):
            return {
                "exists": False,
                "birth_date": None,
                "path": self.path,
                "error": None,
            }
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise SystemAgeProfileError("system age profile is not an object")
            birth_date = _validated_iso_date(payload.get("birth_date"))
            return {
                "exists": True,
                "birth_date": birth_date,
                "path": self.path,
                "error": None,
                "format_version": int(payload.get("format_version") or PROFILE_VERSION),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError, SystemAgeProfileError) as exc:
            # Existing-but-corrupt is still locked. Never let a damaged file turn
            # into an opportunity to submit a different DOB through the UI.
            return {
                "exists": True,
                "birth_date": None,
                "path": self.path,
                "error": str(exc),
            }

    def write_once(self, birth_date):
        wanted = _validated_iso_date(birth_date)
        current = self.read()
        if current["exists"]:
            if current.get("birth_date") == wanted and not current.get("error"):
                return False
            raise BirthDateLockedError(
                "Birth date is already locked to this operating-system user profile."
            )

        directory = os.path.dirname(self.path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

        payload = json.dumps(
            {
                "format_version": PROFILE_VERSION,
                "birth_date": wanted,
                "locked": True,
                "created_utc": datetime.datetime.now(datetime.timezone.utc).replace(
                    microsecond=0
                ).isoformat().replace("+00:00", "Z"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            raise BirthDateLockedError(
                "Birth date is already locked to this operating-system user profile."
            )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(self.path, 0o400)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            raise
        return True
