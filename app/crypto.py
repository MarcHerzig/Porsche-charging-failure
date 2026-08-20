"""Verschluesselung von Zugangsdaten at-rest.

Nutzt einen Fernet-Key, der entweder ueber die Env-Var ENCRYPTION_KEY
uebergeben wird (empfohlen fuer produktive Deployments, z.B. via ArgoCD-
Secret) oder beim ersten Start automatisch erzeugt und in DATA_DIR
persistiert wird (einfacher Onboarding-Fall fuer Docker-Compose-Nutzer).
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

from . import config


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("ENCRYPTION_KEY")
    if env_key:
        return env_key.encode("utf-8")

    if config.ENCRYPTION_KEY_FILE.exists():
        return config.ENCRYPTION_KEY_FILE.read_bytes().strip()

    key = Fernet.generate_key()
    config.ENCRYPTION_KEY_FILE.write_bytes(key)
    os.chmod(config.ENCRYPTION_KEY_FILE, 0o600)
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt(value: str | None) -> bytes | None:
    if value is None or value == "":
        return None
    return _fernet.encrypt(value.encode("utf-8"))


def decrypt(value: bytes | None) -> str | None:
    if value is None:
        return None
    return _fernet.decrypt(value).decode("utf-8")
