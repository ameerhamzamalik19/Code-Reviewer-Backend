from email_validator import validate_email, EmailNotValidError
import re
from sqlalchemy.orm import Session
from database.models import User

class ReviewSessionUpdateError(Exception):
    """Custom exception for update failures."""
    pass

def is_valid_email(email: str) -> bool:
    try:
        # The `validate_email` function does the heavy lifting.
        # `check_deliverability=True` will also check if the domain has valid MX records.
        # For a signup form, you generally want this check.
        validation = validate_email(email, check_deliverability=True)
        # The library also provides a normalized form of the email, which is good to store.
        normalized_email = validation.normalized
        return normalized_email
    except EmailNotValidError as e:
        # The exception message (`str(e)`) is a human-readable explanation.
        print(f"Invalid email: {e}")
        return False

def check_user_exists(db: Session, username: str, email: str):
    """
    Raises ValueError if a user with the given username or email already exists.
    """
    existing = db.query(User).filter(
        (User.user_name == username) | (User.email == email)
    ).first()
    if existing:
        if existing.user_name == username:
            raise ValueError("Username already taken")
        else:  # email matches
            raise ValueError("Email already registered")

def create_user_validator(db: Session, username: str, email: str, password: str):

    if not username or not email or not password:
        raise ValueError("Username, email, and password are required")

    if not re.match(r'^[A-Za-z0-9_]+$', username):
        raise ValueError("Username can only contain letters, numbers, and underscores")
    
    if len(username) < 3 or len(username) > 100:
        raise ValueError("Username must be between 3 and 100 characters long")
    
    if len(email) > 320 or len(email) <= 0:
        raise ValueError("Email must be 320 characters or fewer")
    
    is_email_valid = is_valid_email(email)

    if not is_email_valid:
        raise ValueError("Invalid email format")
    
    if len(password) < 6 or len(password) > 65:
        raise ValueError("Password must be between 6 and 65 characters long")
    
    try:
        check_user_exists(db, username, email)
    except ValueError as ve:
        print(f"User existence check failed: {ve}")
        raise ve
    
    normalized_email = is_email_valid  # This is the normalized email returned by the validator

    return normalized_email