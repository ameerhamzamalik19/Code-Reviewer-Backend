import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Use a single DATABASE_URL in your .env
# Example: postgresql://user:password@localhost:5432/code_reviewer_db
DATABASE_URL = os.getenv("POSTGRES_DATABASE_URL")

# Engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    echo=False  # Set to True to see SQL logs during development
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all models
Base = declarative_base()

# FastAPI Dependency: provides a session and ensures it closes after the request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()