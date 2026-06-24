from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database.models import User, ReviewSession
import bcrypt
from database.validators import create_user_validator, ReviewSessionUpdateError
from typing import Any

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

def create_review_session(db: Session, user: User, repo_type: str, pr_url: str, repo_path: str = "None"):
    new_session = ReviewSession(
        user_id = user.user_id,
        type = repo_type,
        pr_url = pr_url,
        repo_path = repo_path
    )

    db.add(new_session)
    try:
        db.commit()
        db.refresh(new_session)  # Loads the auto-generated ID into the object
        return new_session.session_id
    except IntegrityError as e:
        db.rollback()  # VERY IMPORTANT: rollback the failed transaction

        raise  ValueError(e)

    
def update_ReviewSession(
    session_id: int,
    column_name: str,
    new_value: Any,
    db_session: Session,
    commit: bool = True
) -> ReviewSession:
    """
    Update a single column of a ReviewSession record.

    Args:
        session_id: Primary key of the session to update.
        column_name: Name of the column to update (must exist in ReviewSession).
        new_value: New value to set for the column.
        db_session: SQLAlchemy session.
        commit: If True, commit the transaction; otherwise only flush.

    Returns:
        The updated ReviewSession object.

    Raises:
        ReviewSessionUpdateError: If column is invalid, session not found,
                                  or a database error occurs.
    """
    # 1. Validate that the column exists in the ReviewSession model
    if not hasattr(ReviewSession, column_name):
        raise ReviewSessionUpdateError(
            f"Column '{column_name}' does not exist in ReviewSession schema."
        )

    # Optional: prevent updating primary key or sensitive columns
    if column_name == "session_id":
        raise ReviewSessionUpdateError("Updating primary key 'session_id' is not allowed.")

    # 2. Retrieve the session record
    session_record = db_session.query(ReviewSession).filter(
        ReviewSession.session_id == session_id
    ).first()

    if session_record is None:
        raise ReviewSessionUpdateError(
            f"ReviewSession with session_id {session_id} not found."
        )

    # 3. Update the attribute
    try:
        setattr(session_record, column_name, new_value)
        # The `updated_at` column is automatically handled by `onupdate` if defined
        if commit:
            db_session.commit()
        else:
            db_session.flush()
    except SQLAlchemyError as e:
        db_session.rollback()
        raise ReviewSessionUpdateError(
            f"Database error while updating column '{column_name}': {str(e)}"
        ) from e

    # 4. Refresh to get any DB-generated values (e.g., updated_at)
    db_session.refresh(session_record)
    return session_record
