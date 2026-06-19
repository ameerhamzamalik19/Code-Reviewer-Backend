from fastapi import Request, Response, HTTPException, Depends
from sqlalchemy.orm import Session
from jose import jwt, JWTError, ExpiredSignatureError

from database.database import get_db
from database.models import User
from redis_client import get_refresh_token
from auth_config import SECRET_KEY, ALGORITHM, create_access_token


# ---------- Reusable helpers ----------

def extract_access_token(request: Request) -> str:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise ValueError("Missing or invalid access token")
    return auth_header.split(" ")[1]


def decode_jwt_token(token: str, expected_type: str = None) -> dict:
    """
    Decode and validate a JWT.
    If expected_type is given, also verify the 'type' claim.
    Raises HTTPException for general JWT errors, but lets ExpiredSignatureError propagate.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise   # let caller handle refresh
    except JWTError:
        raise ValueError("Invalid token")

    if expected_type and payload.get("type") != expected_type:
        raise ValueError("Invalid token type")
    return payload


def get_user_by_username(db: Session, username: str) -> User:
    """Fetch user from DB or raise 401."""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise ValueError("User not found")
    return user


def validate_refresh_token_in_redis(user_id: str, refresh_token: str) -> bool:
    """Check if the given refresh token matches the one stored in Redis."""
    stored = get_refresh_token(user_id)
    return bool(stored and stored == refresh_token)


def refresh_access_token(username: str) -> str:
    """Generate a new access token."""
    return create_access_token(data={"sub": username})


# ---------- Main dependency ----------

async def get_current_user_auto_refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
) -> User:
    """
    Dependency that returns the current authenticated user.
    - If the access token is valid, returns the user.
    - If the access token has expired, attempts to refresh using the refresh token cookie.
    - On successful refresh, attaches the new access token to the response header.
    - If refresh fails (missing token, mismatch, or Redis miss), raises 401.
    """
    # 1. Extract access token
    try:
        token = extract_access_token(request)
    except ValueError as ve:
        raise ValueError(f"Access token error: {ve}")

    try:
        # 2. Try to decode and validate the access token
        payload = decode_jwt_token(token)
        username = payload.get("sub")
        if not username:
            raise ValueError("Invalid token payload")

        # 3. Fetch user – all good, return it
        return get_user_by_username(db, username)

    except ExpiredSignatureError:
        # 4. Access token expired – try to refresh
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            raise ValueError("Refresh token missing")

        # 5. Decode refresh token (must have type="refresh")
        try:
            refresh_payload = decode_jwt_token(refresh_token, expected_type="refresh")
        except ValueError as ve:
            # re‑raise with a more specific message, or just let it bubble
            raise ValueError("Invalid refresh token")

        username = refresh_payload.get("sub")
        if not username:
            raise ValueError("Invalid refresh token payload")

        # 6. Fetch user from DB
        try:
            user = get_user_by_username(db, username)
        except ValueError as ve:
            raise ValueError("User not found")

        # 7. Verify refresh token against Redis
        if not validate_refresh_token_in_redis(str(user.user_id), refresh_token):
            raise ValueError(
                "Session expired. Please log in again."
            )

        # 8. All checks passed – issue new access token and attach to response
        new_access_token = refresh_access_token(username)
        response.headers["X-New-Access-Token"] = new_access_token

        # 9. Return the user object (the original request can now proceed)
        return user