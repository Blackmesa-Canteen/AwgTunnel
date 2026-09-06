"""Persistence for profiles, with private keys held in the system keyring.

Profile text is stored in the application's own data directory with the private
key replaced by a placeholder; the key itself goes to the Secret Service via
libsecret. If no keyring is available the key is kept in the same 0600 file
instead — the application stays usable, but records that it did so, and the UI
says as much rather than quietly downgrading the user's security.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

from .build_config import APP_ID  # noqa: E402
from .profile import Profile, ProfileError, parse  # noqa: E402

_PLACEHOLDER = "<stored-in-keyring>"
_STORE_VERSION = 1

_KEY_SCHEMA = Secret.Schema.new(
    f"{APP_ID}.PrivateKey",
    Secret.SchemaFlags.NONE,
    {"profile": Secret.SchemaAttributeType.STRING},
)

_PRIVATE_KEY_RE = re.compile(
    r"^(?P<prefix>\s*PrivateKey\s*=\s*)(?P<value>.*)$",
    re.IGNORECASE | re.MULTILINE,
)


class StorageError(RuntimeError):
    """Profiles could not be read or written."""


@dataclass
class StoredProfile:
    id: str
    name: str
    text: str
    key_in_keyring: bool

    @property
    def is_secret_on_disk(self) -> bool:
        return not self.key_in_keyring


def data_dir() -> Path:
    directory = Path(GLib.get_user_data_dir()) / "awg-tunnel"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)
    return directory


def _store_path() -> Path:
    return data_dir() / "profiles.json"


def _split_private_key(text: str) -> tuple[str, str | None]:
    """Return ``text`` with the private key replaced, plus the key itself."""
    match = _PRIVATE_KEY_RE.search(text)
    if match is None:
        return text, None
    key = match.group("value").strip()
    if not key or key == _PLACEHOLDER:
        return text, None
    redacted = text[: match.start("value")] + _PLACEHOLDER + text[match.end("value") :]
    return redacted, key


def _inject_private_key(text: str, key: str) -> str:
    return _PRIVATE_KEY_RE.sub(lambda m: f"{m.group('prefix')}{key}", text, count=1)


def _keyring_store(profile_id: str, key: str) -> bool:
    try:
        return bool(
            Secret.password_store_sync(
                _KEY_SCHEMA,
                {"profile": profile_id},
                Secret.COLLECTION_DEFAULT,
                f"AWG Tunnel private key ({profile_id})",
                key,
                None,
            )
        )
    except GLib.Error:
        return False


def _keyring_lookup(profile_id: str) -> str | None:
    try:
        return Secret.password_lookup_sync(_KEY_SCHEMA, {"profile": profile_id}, None)
    except GLib.Error:
        return None


def _keyring_clear(profile_id: str) -> None:
    with contextlib.suppress(GLib.Error):
        Secret.password_clear_sync(_KEY_SCHEMA, {"profile": profile_id}, None)


class ProfileStore:
    """A small JSON-backed collection of profiles."""

    def __init__(self) -> None:
        self._entries: list[dict[str, object]] = []
        self.load()

    def load(self) -> None:
        path = _store_path()
        if not path.exists():
            self._entries = []
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"Could not read saved profiles: {exc}") from exc

        entries = raw.get("profiles") if isinstance(raw, dict) else None
        self._entries = [e for e in entries or [] if isinstance(e, dict)]

    def _flush(self) -> None:
        path = _store_path()
        payload = json.dumps(
            {"version": _STORE_VERSION, "profiles": self._entries}, indent=2
        )
        temporary = path.with_suffix(".json.tmp")
        try:
            handle = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        except OSError as exc:
            raise StorageError(f"Could not save profiles: {exc}") from exc

    def list(self) -> list[StoredProfile]:
        result: list[StoredProfile] = []
        for entry in self._entries:
            result.append(
                StoredProfile(
                    id=str(entry.get("id", "")),
                    name=str(entry.get("name", "Unnamed")),
                    text=str(entry.get("text", "")),
                    key_in_keyring=bool(entry.get("key_in_keyring", False)),
                )
            )
        return result

    def get(self, profile_id: str) -> StoredProfile | None:
        for stored in self.list():
            if stored.id == profile_id:
                return stored
        return None

    def add(self, name: str, text: str) -> StoredProfile:
        """Validate, then persist a profile. Raises on malformed input."""
        parse(text)  # reject invalid profiles before they are ever stored

        profile_id = uuid.uuid4().hex
        redacted, key = _split_private_key(text)
        if key is None:
            raise ProfileError("[Interface] is missing required setting 'PrivateKey'")

        in_keyring = _keyring_store(profile_id, key)
        stored_text = redacted if in_keyring else text

        self._entries.append(
            {
                "id": profile_id,
                "name": name.strip() or "Unnamed",
                "text": stored_text,
                "key_in_keyring": in_keyring,
            }
        )
        self._flush()
        return StoredProfile(
            id=profile_id, name=name, text=stored_text, key_in_keyring=in_keyring
        )

    def rename(self, profile_id: str, name: str) -> None:
        for entry in self._entries:
            if entry.get("id") == profile_id:
                entry["name"] = name.strip() or "Unnamed"
                self._flush()
                return
        raise StorageError("No such profile")

    def remove(self, profile_id: str) -> None:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get("id") != profile_id]
        if len(self._entries) == before:
            raise StorageError("No such profile")
        _keyring_clear(profile_id)
        self._flush()

    def resolve(self, profile_id: str) -> Profile:
        """Return a connectable profile, re-injecting the key from the keyring."""
        stored = self.get(profile_id)
        if stored is None:
            raise StorageError("No such profile")

        text = stored.text
        if stored.key_in_keyring:
            key = _keyring_lookup(profile_id)
            if not key:
                raise StorageError(
                    "The private key for this profile is missing from the keyring. "
                    "Re-import the profile."
                )
            text = _inject_private_key(text, key)
        return parse(text)
