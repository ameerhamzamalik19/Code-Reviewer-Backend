from database.database import Base
from sqlalchemy import Column, Integer, String, DateTime
# from datetime import datetime
from datetime import datetime, timezone

class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # created_at = Column(DateTime, default=datetime.now(datetime.timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def verify_password(self, password: str) -> bool:
        # This method will be implemented in the future to verify the password using bcrypt
        pass