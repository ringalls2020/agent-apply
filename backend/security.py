from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from common.security import (
    SecurityError,
    create_body_signature,
    create_hs256_jwt,
    sha256_hex,
    verify_body_signature,
    verify_hs256_jwt,
)


def _to_bytes(value: str) -> bytes:
    return value.encode("utf-8")


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        _to_bytes(password),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hash_hex)
    except ValueError:
        return False

    actual = hashlib.scrypt(
        _to_bytes(password),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


def _fernet_from_secret(secret: str) -> Fernet:
    raw = secret.strip().encode("utf-8")
    if not raw:
        raise SecurityError("USER_PROFILE_ENCRYPTION_KEY is required")

    # Accept either a pre-generated Fernet key or a passphrase-like secret.
    try:
        return Fernet(raw)
    except Exception:
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
        return Fernet(derived)


def _profile_encryption_secret() -> str:
    return os.getenv("USER_PROFILE_ENCRYPTION_KEY", "").strip()


@lru_cache(maxsize=1)
def _profile_fernet() -> Fernet:
    secret = _profile_encryption_secret()
    if not secret:
        raise SecurityError("USER_PROFILE_ENCRYPTION_KEY is required")
    return _fernet_from_secret(secret)


def validate_profile_encryption_config(*, required: bool) -> None:
    secret = _profile_encryption_secret()
    if not secret:
        if required:
            raise SecurityError("USER_PROFILE_ENCRYPTION_KEY is required")
        return
    _fernet_from_secret(secret)


def encrypt_sensitive_text(value: str) -> str:
    if value == "":
        return ""
    return _profile_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_sensitive_text(value: str) -> str:
    if value == "":
        return ""
    try:
        return _profile_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecurityError("Failed to decrypt sensitive profile field") from exc
