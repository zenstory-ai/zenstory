#!/usr/bin/env python3
"""Create an administrator from explicit environment-provided credentials.

Required environment variables:
- ZENSTORY_ADMIN_EMAIL
- ZENSTORY_ADMIN_PASSWORD

ZENSTORY_ADMIN_USERNAME is optional and defaults to ``admin``.
"""
import os
import sys

# Load .env file
from dotenv import load_dotenv

load_dotenv()

# Use DATABASE_URL from env, fallback to SQLite for local dev
os.environ.setdefault("DATABASE_URL", "sqlite:///./zenstory.db")

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passlib.context import CryptContext  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

from database import sync_engine  # noqa: E402
from models import User  # noqa: E402


def main() -> int:
    email = os.getenv("ZENSTORY_ADMIN_EMAIL", "").strip()
    password = os.getenv("ZENSTORY_ADMIN_PASSWORD", "")
    username = os.getenv("ZENSTORY_ADMIN_USERNAME", "admin").strip() or "admin"

    if not email or not password:
        print(
            "ZENSTORY_ADMIN_EMAIL and ZENSTORY_ADMIN_PASSWORD are required.",
            file=sys.stderr,
        )
        return 2
    if len(password) < 12:
        print("ZENSTORY_ADMIN_PASSWORD must be at least 12 characters.", file=sys.stderr)
        return 2

    # Create tables
    print("Creating tables...")
    SQLModel.metadata.create_all(sync_engine)
    print("Tables created.")

    # Create password hash
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed_password = pwd_context.hash(password)

    # Create admin user
    with Session(sync_engine) as session:
        existing = session.query(User).filter(User.email == email).first()
        if existing:
            print(f"Admin user already exists: {existing.email}")
        else:
            admin = User(
                email=email,
                username=username,
                hashed_password=hashed_password,
                is_active=True,
                is_superuser=True,
                email_verified=True,
            )
            session.add(admin)
            session.commit()
            print("Admin user created:")
            print(f"  Email: {email}")
            print(f"  Username: {username}")
            print("  Password: [REDACTED]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
