from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app import config

_hasher = PasswordHash.recommended()  # argon2

# Verified against when the email doesn't exist, so login takes the same time either way.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


# Hash a password for storage
def hash_password(password: str) -> str:
    """Hash a plain-text password with argon2 for storing in users.password_hash."""
    return _hasher.hash(password)


# Check a password in constant time, even for unknown users
def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password against a stored hash; pass None when the user does not exist (always False, same timing)."""
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    return _hasher.verify(password, password_hash)


# Issue a JWT for a user
def create_access_token(user_id: int) -> str:
    """Create a signed JWT for the user that expires after JWT_EXPIRE_MINUTES."""
    expires = datetime.now(UTC) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(user_id), "exp": expires}, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


# Read the user id back out of a JWT
def decode_access_token(token: str) -> int | None:
    """Return the user id from a valid token, or None if it is invalid or expired."""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
