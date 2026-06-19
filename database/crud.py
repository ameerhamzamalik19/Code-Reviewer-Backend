from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database.models import User
import bcrypt
from email_validator import validate_email, EmailNotValidError
import re

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

def create_user(db: Session, username: str, email: str, password: str):
    """
    Creates a new user.
    Raises ValueError if username or email is already taken.
    """
    try: 
        normalized_email = create_user_validator(db, username, email, password)
    except ValueError as ve:
        raise ValueError(f"Validation error: {ve}")
        # raise HTTPException(status_code=400, detail=str(ve))

    # 1. Hash the password
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    # 2. Create the ORM object
    new_user = User(
        user_name=username,
        email=normalized_email,
        password_hash=password_hash
    )

    # 3. Add to session and commit (this is your "execute_query")
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)  # Loads the auto-generated ID into the object
        return new_user.user_id
    except IntegrityError as e:
        db.rollback()  # VERY IMPORTANT: rollback the failed transaction
        # Check if the error is a duplicate key violation
        if "duplicate key value violates unique constraint" in str(e.orig):
            if "username" in str(e.orig):
                raise ValueError("Username already taken")
            elif "email" in str(e.orig):
                raise ValueError("Email already registered")
        raise  # Re-raise if it's a different integrity error

def login_user(db: Session, username: str, password: str):
    """
    Authenticates a user by username and password.
    Returns the User object if successful, or raises ValueError if authentication fails.
    """
    user = db.query(User).filter(User.user_name == username).first()
    if not user:
        raise ValueError("Invalid username")

    if not bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
        raise ValueError("Invalid password")

    return user