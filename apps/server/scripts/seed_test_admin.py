#!/usr/bin/env python3
"""Seed E2E test admin user in database."""
import os
import sys

# Load .env.test file
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test"))

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passlib.context import CryptContext  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from database import sync_engine  # noqa: E402
from models import User  # noqa: E402


def main():
    # Get test admin credentials from environment or use defaults
    email = os.getenv("E2E_TEST_ADMIN_EMAIL", "test-admin@zenstory.test")
    password = os.getenv("E2E_TEST_ADMIN_PASSWORD", "TestAdmin123!")
    username = os.getenv("E2E_TEST_ADMIN_USERNAME", "test-admin")

    # Create password hash
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed_password = pwd_context.hash(password)

    # Create or update test admin user
    with Session(sync_engine) as session:
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing:
            # Update existing user (idempotent)
            existing.hashed_password = hashed_password
            existing.username = username
            existing.email_verified = True
            existing.is_active = True
            existing.is_superuser = True  # Admin must be superuser
            session.add(existing)
            session.commit()
            print(f"Test admin user updated: {existing.email}")
            print(f"  Username: {username}")
            print("  Password: [REDACTED]")
            print(f"  Email verified: {existing.email_verified}")
            print(f"  Active: {existing.is_active}")
            print(f"  Superuser: {existing.is_superuser}")
        else:
            # Create new test admin user
            test_admin = User(
                email=email,
                username=username,
                hashed_password=hashed_password,
                email_verified=True,     # CRITICAL: Must be True for login
                is_active=True,          # CRITICAL: Must be True for login
                is_superuser=True,       # CRITICAL: Must be True for admin
            )
            session.add(test_admin)
            session.commit()
            print("Test admin user created:")
            print(f"  Email: {email}")
            print(f"  Username: {username}")
            print("  Password: [REDACTED]")
            print(f"  Email verified: {test_admin.email_verified}")
            print(f"  Active: {test_admin.is_active}")
            print(f"  Superuser: {test_admin.is_superuser}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
