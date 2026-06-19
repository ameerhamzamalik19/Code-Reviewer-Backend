from jose import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from auth.auth_config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
import uuid
from fastapi import Request
from jose import JWTError, ExpiredSignatureError, jwt
from sqlalchemy.orm import Session
from database.models import User
from redis_client import get_refresh_token, delete_refresh_token

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token using python-jose.

    Args:
        data: Dictionary of claims (e.g., {"sub": username}).
        expires_delta: Optional custom expiration.

    Returns:
        Encoded JWT string.
    """

    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp": expire,
        "iat": now,             # issued at – changes every call
        "type": "access"         # token type claim for clarity
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a longer-lived JWT refresh token.
    Typically used to obtain new access tokens without re‑authentication.
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    print(f"Creating refresh token with data: {data} and expires_delta: {expire}")  # Debugging line

    to_encode.update({
            "exp": expire,
            "iat": now,                 # issued at – changes every call
            "jti": str(uuid.uuid4()),
            "type": "refresh"             # token type claim for clarity
        })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_refresh_token_from_cookie(request: Request) -> str:
    """Extract refresh token from the HttpOnly cookie."""
    refresh_token = request.cookies.get("refresh_token")
    print(f"Extracted refresh token from cookie: {refresh_token}")  # Debugging line
    if not refresh_token:
        raise ValueError("Refresh token missing")
    
    return refresh_token

def validate_refresh_token(token: str, db: Session) -> User:
    """
    Validate a refresh token:
    - Verify signature and expiry.
    - Check that it matches the token stored in Redis for the user.
    Returns the user object if valid, raises ValueError otherwise.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise ValueError("Invalid refresh token")

    username = payload.get("sub")
    token_type = payload.get("type")
    if not username or token_type != "refresh":
        raise ValueError("Invalid refresh token")

    # Check against Redis
    stored_token = get_refresh_token(username)
    if stored_token is None or stored_token.decode() != token:  # stored_token is bytes
        raise ValueError("Refresh token revoked or expired")

    # Get user from DB
    user = db.query(User).filter(User.user_name == username).first()
    if not user:
        raise ValueError("User not found")
    return user
