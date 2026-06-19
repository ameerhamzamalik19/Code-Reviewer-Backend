from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from database.crud import create_user
from fastapi import HTTPException

class SignupValidator(BaseModel):
    username: str
    email: str
    password: str
    user_id: Optional[int] = None  # Optional field for user ID, useful for updates

    def signup_user(self, db: Session):
        """
        Validates the signup data and creates a new user in the database.
        Raises HTTPException with status code 400 if validation fails.
        """
        try: 
            self.user_id = create_user(db, self.username, self.email, self.password)

            return True
        
        except ValueError as ve:
            raise ValueError(f"Validation error during signup: {ve}")