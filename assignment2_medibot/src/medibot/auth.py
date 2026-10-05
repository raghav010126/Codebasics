"""Demo user store + JWT session tokens. The role in the token is the only trusted role."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from medibot.config import get_settings
from medibot.rbac import ROLES

_ITERATIONS = 200_000

# username -> (role, salt, pbkdf2-sha256 hash). Demo passwords are listed in the README.
USERS: dict[str, tuple[str, str, str]] = {
    "dr.mehta": ("doctor", "51ff5e0bae6df0b3", "7654b2ec9a89d1682edc62545e7f7e60d261b74f15fb4154c8bf8e1ea52167dd"),
    "nurse.priya": ("nurse", "0cc6fb8589f260a1", "5fc865df7f51917ce8c26c0f78e4eebb0a48a3eda3d5a143e2bc22c255ef2410"),
    "billing.ravi": ("billing_executive", "65dfa91ee48aeddc", "78cb1b71868702496c09adfb69c58e0e019e4268430f000e70378c1207efe633"),
    "tech.anand": ("technician", "175e7fc947614a53", "2c7f165ee4ab9b59c7c0b0dcc63dd74fb72473fe06cc0e401335a235caacebfb"),
    "admin.sys": ("admin", "ce9e00f3a21a230f", "c411c895b9ce947880f39be3edd55d432b0919dd7d4ce60ae78ea2bcba852d5e"),
}
_DUMMY_SALT = "0" * 16


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()


def authenticate(username: str, password: str) -> tuple[str, str] | None:
    """Return (username, role) for valid credentials. Constant work for unknown users."""
    role, salt, expected = USERS.get(username, ("", _DUMMY_SALT, ""))
    ok = hmac.compare_digest(_hash(password, salt), expected) if expected else False
    return (username, role) if ok else None


def create_token(username: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    claims = {"sub": username, "role": role, "iat": now, "exp": now + timedelta(minutes=s.jwt_ttl_minutes)}
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> tuple[str, str]:
    s = get_settings()
    try:
        claims = jwt.decode(
            token, s.jwt_secret, algorithms=[s.jwt_algorithm],  # algorithm pinned: rejects alg=none
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
                            headers={"WWW-Authenticate": "Bearer"}) from e
    if claims["role"] not in ROLES or USERS.get(claims["sub"], ("",))[0] != claims["role"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token claims.")
    return claims["sub"], claims["role"]


_bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> tuple[str, str]:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.",
                            headers={"WWW-Authenticate": "Bearer"})
    return decode_token(cred.credentials)
