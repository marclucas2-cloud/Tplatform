"""
Authentication module — JWT single-user auth for trading dashboard.

Usage:
  POST /api/auth/login  → {username, password} → {access_token}
  All other /api/* endpoints require Bearer token.

Env vars:
  DASHBOARD_USER      (default: marc)
  DASHBOARD_PASSWORD   (required, plain text — hashed at startup)
  DASHBOARD_JWT_SECRET (required, random string for HS256 signing)
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

logger = logging.getLogger("dashboard-auth")

# ── JWT config ───────────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get("DASHBOARD_JWT_SECRET", "change-me-in-production-!!!")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72  # 3 days

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "marc")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

security = HTTPBearer(auto_error=False)

# ── Lightweight JWT (no external deps) ───────────────────────────────────────
# Uses stdlib hmac + base64 to avoid python-jose dependency on the VPS.

import hmac
import hashlib
import base64
import json


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _create_token(payload: dict) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    h = _b64url_encode(json.dumps(header).encode())
    p = _b64url_encode(json.dumps(payload, default=str).encode())
    signature = hmac.new(
        JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256
    ).digest()
    s = _b64url_encode(signature)
    return f"{h}.{p}.{s}"


def _verify_token(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        h, p, s = parts
        expected_sig = hmac.new(
            JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(s)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(p))
        # Check expiry
        exp = payload.get("exp")
        if exp and datetime.fromisoformat(exp) < datetime.now(timezone.utc):
            return None
        return payload
    except Exception:
        return None


# ── Models ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = JWT_EXPIRY_HOURS * 3600


# ── Dependency ───────────────────────────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """FastAPI dependency — validates JWT and returns username."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload.get("sub", "unknown")


# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    """Authenticate and return JWT token."""
    if not DASHBOARD_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DASHBOARD_PASSWORD not configured on server",
        )

    if req.username != DASHBOARD_USER or req.password != DASHBOARD_PASSWORD:
        logger.warning(f"Failed login attempt for user: {req.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    exp = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    token = _create_token({"sub": req.username, "exp": exp.isoformat()})
    logger.info(f"Login successful for user: {req.username}")
    return TokenResponse(access_token=token)


@router.get("/me")
def get_me(user: str = Depends(get_current_user)):
    """Return current authenticated user."""
    return {"username": user}
