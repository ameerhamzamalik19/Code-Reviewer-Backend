from pydantic import BaseModel
from sqlalchemy.orm import Session
from database.crud import login_user

class LoginValidator(BaseModel):
    username: str
    password: str

    def validate_login(self, db: Session):
        # 1. Fetch user by username

        try:
            user = login_user(db, self.username, self.password)
            return user
        except ValueError as ve:
            raise ValueError(f"Login validation failed: {ve}")

