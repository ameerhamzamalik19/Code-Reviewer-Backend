from database.database import Base
from sqlalchemy import Column, Integer, String, DateTime, text, Text
from datetime import datetime, timezone

        # USERS TABLE
class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # created_at = Column(DateTime, default=datetime.now(datetime.timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


        # REVIEWED_SESSIONS TABLE
class ReviewSession(Base):
    __tablename__ = "reviewed_sessions"

    session_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)

    type = Column(String(20))
    pr_url = Column(Text, nullable=False)
    repo_path = Column(Text, nullable=False)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )