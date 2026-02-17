from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


class SecurityError(ValueError):
    pass


def _to_bytes(value: str) -> bytes:
    return value.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_hs256_jwt(
    *,
    payload: Dict[str, Any],
    secret: str,
    issuer: str,
    audience: str,
    expires_in_seconds: int = 300,
) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        **payload,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }

    header = {"alg": "HS256", "typ": "JWT"}
    header_segment = _b64url_encode(_to_bytes(json.dumps(header, separators=(",", ":"))))
    payload_segment = _b64url_encode(_to_bytes(json.dumps(claims, separators=(",", ":"))))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")

    signature = hmac.new(_to_bytes(secret), signing_input, hashlib.sha256).digest()
    signature_segment = _b64url_encode(signature)

    return f"{header_segment}.{payload_segment}.{signature_segment}"


def verify_hs256_jwt(
    *,
    token: str,
    secret: str,
    audience: str,
    issuer: str | None = None,
) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise SecurityError("Malformed JWT")

    header_segment, payload_segment, signature_segment = parts

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = hmac.new(
        _to_bytes(secret), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_signature, _b64url_decode(signature_segment)):
        raise SecurityError("JWT signature mismatch")

    header = json.loads(_b64url_decode(header_segment))
    if header.get("alg") != "HS256":
        raise SecurityError("Unsupported JWT algorithm")

    claims: Dict[str, Any] = json.loads(_b64url_decode(payload_segment))
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if claims.get("aud") != audience:
        raise SecurityError("JWT audience mismatch")
    if issuer and claims.get("iss") != issuer:
        raise SecurityError("JWT issuer mismatch")
    if int(claims.get("exp", 0)) < now_ts:
        raise SecurityError("JWT expired")

    return claims


def create_body_signature(*, body: bytes, timestamp: str, nonce: str, secret: str) -> str:
    payload = b".".join([_to_bytes(timestamp), _to_bytes(nonce), body])
    digest = hmac.new(_to_bytes(secret), payload, hashlib.sha256).hexdigest()
    return digest


def verify_body_signature(
    *, body: bytes, timestamp: str, nonce: str, secret: str, signature: str
) -> bool:
    expected = create_body_signature(
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        secret=secret,
    )
    return hmac.compare_digest(expected, signature)


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
