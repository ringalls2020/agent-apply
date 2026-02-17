from __future__ import annotations

from fastapi import HTTPException, Request, status

from .security import SecurityError, create_hs256_jwt, verify_hs256_jwt


def extract_bearer_token(auth_header: str | None) -> str:
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    parts = auth_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )
    return parts[1].strip()


def create_user_access_token(request: Request, user_id: str) -> str:
    return create_hs256_jwt(
        payload={"sub": user_id},
        secret=request.app.state.user_auth_signing_secret,
        issuer=request.app.state.user_auth_issuer,
        audience=request.app.state.user_auth_audience,
        expires_in_seconds=request.app.state.user_auth_token_ttl_seconds,
    )


def authenticated_user_id_from_request(request: Request) -> str:
    token = extract_bearer_token(request.headers.get("authorization"))
    try:
        claims = verify_hs256_jwt(
            token=token,
            secret=request.app.state.user_auth_signing_secret,
            audience=request.app.state.user_auth_audience,
            issuer=request.app.state.user_auth_issuer,
        )
    except SecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user auth token: {exc}",
        ) from exc

    user_id = str(claims.get("sub", "")).strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User auth token missing subject",
        )
    return user_id


def require_user_id_match(request: Request, user_id: str) -> str:
    authenticated_user_id = authenticated_user_id_from_request(request)
    if authenticated_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's profile",
        )
    return authenticated_user_id
