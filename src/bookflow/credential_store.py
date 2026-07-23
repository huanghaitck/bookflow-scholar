"""Secret resolution without persisting plaintext credentials in Bookflow config."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


SERVICE = "Bookflow"
_PROCESS_SECRETS: dict[str, str] = {}


class SecretBackend(Protocol):
    name: str

    def get(self, alias: str) -> str | None: ...
    def set(self, alias: str, secret: str) -> None: ...
    def delete(self, alias: str) -> bool: ...


class KeyringBackend:
    name = "python-keyring"

    def __init__(self) -> None:
        import keyring
        self._keyring = keyring

    def get(self, alias: str) -> str | None:
        return self._keyring.get_password(SERVICE, alias)

    def set(self, alias: str, secret: str) -> None:
        self._keyring.set_password(SERVICE, alias, secret)

    def delete(self, alias: str) -> bool:
        try:
            self._keyring.delete_password(SERVICE, alias)
            return True
        except Exception:
            return False


class WindowsCredentialBackend:
    """Small standard-library adapter for Windows Credential Manager."""

    name = "windows-credential-manager"
    _GENERIC = 1
    _LOCAL_MACHINE = 2

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager is unavailable")
        self._api = ctypes.WinDLL("advapi32", use_last_error=True)
        self._api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.POINTER(ctypes.POINTER(self._CREDENTIAL))]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredWriteW.argtypes = [ctypes.POINTER(self._CREDENTIAL), wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _target(alias: str) -> str:
        return f"{SERVICE}:{alias}"

    def get(self, alias: str) -> str | None:
        pointer = ctypes.POINTER(self._CREDENTIAL)()
        if not self._api.CredReadW(self._target(alias), self._GENERIC, 0, ctypes.byref(pointer)):
            return None
        try:
            value = pointer.contents
            raw = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            self._api.CredFree(pointer)

    def set(self, alias: str, secret: str) -> None:
        raw = secret.encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        value = self._CREDENTIAL(Type=self._GENERIC, TargetName=self._target(alias),
                                 CredentialBlobSize=len(raw), CredentialBlob=blob,
                                 Persist=self._LOCAL_MACHINE, UserName=alias)
        if not self._api.CredWriteW(ctypes.byref(value), 0):
            raise OSError(ctypes.get_last_error(), "CredWriteW failed")

    def delete(self, alias: str) -> bool:
        if self._api.CredDeleteW(self._target(alias), self._GENERIC, 0):
            return True
        return ctypes.get_last_error() == 1168


def default_backend() -> SecretBackend | None:
    try:
        return KeyringBackend()
    except Exception:
        pass
    if os.name == "nt":
        return WindowsCredentialBackend()
    return None


@dataclass
class CredentialStore:
    backend: SecretBackend | None = None

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = default_backend()

    def list(self) -> dict[str, object]:
        return {"backend": self.backend.name if self.backend else "process-only",
                "process_aliases": sorted(_PROCESS_SECRETS)}

    def set(self, alias: str, secret: str, *, process_only: bool = False) -> dict[str, object]:
        if not alias.strip() or not secret:
            raise ValueError("alias and secret are required")
        if process_only:
            _PROCESS_SECRETS[alias] = secret
            backend = "process"
        else:
            if self.backend is None:
                raise RuntimeError("no system credential backend is available; use process_only")
            self.backend.set(alias, secret)
            backend = self.backend.name
        return {"alias": alias, "stored": True, "backend": backend}

    def get(self, alias: str) -> str | None:
        return _PROCESS_SECRETS.get(alias) or (self.backend.get(alias) if self.backend else None)

    def delete(self, alias: str) -> dict[str, object]:
        removed = _PROCESS_SECRETS.pop(alias, None) is not None
        if self.backend:
            removed = self.backend.delete(alias) or removed
        return {"alias": alias, "deleted": removed}

    def test(self, alias: str) -> dict[str, object]:
        return {"alias": alias, "present": bool(self.get(alias)),
                "backend": self.backend.name if self.backend else "process-only"}


def resolve_secret(*, alias: str | None = None, env_name: str | None = None,
                   development_env_file: Path | None = None) -> str | None:
    if alias:
        value = CredentialStore().get(alias)
        if value:
            return value
    if env_name and os.getenv(env_name):
        return os.environ[env_name]
    if development_env_file and development_env_file.is_file() and env_name:
        for raw in development_env_file.read_text("utf-8-sig").splitlines():
            name, separator, value = raw.partition("=")
            if separator and name.strip() == env_name:
                return value.strip().strip('"').strip("'")
    return None


def credential_status(*, alias: str | None = None, env_name: str | None = None) -> dict[str, object]:
    """Return non-secret credential presence and provenance for UI status."""
    store = CredentialStore()
    if alias and store.get(alias):
        return {"present": True, "source": store.backend.name if store.backend else "process"}
    if env_name and os.getenv(env_name):
        return {"present": True, "source": "environment"}
    return {"present": False, "source": "not_configured"}
