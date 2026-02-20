"""
db_migrate.py – Run this ONCE to drop and recreate all tables.
WARNING: This drops all existing data. Only run on a fresh DB or when you intend to reset.

Usage:
    cd backend
    python db_migrate.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.database import engine, Base
# Import all models so Base knows about them
from app.models import User, JobDescription, JobApplication, Resume, Notification, ExternalCandidate  # noqa: F401

def reset_db():
    print("⚠️  Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("✅ Tables dropped.")

    print("🔨 Creating all tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ Tables created successfully!")

    # List created tables
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"\n📋 Tables in DB: {', '.join(tables)}")

if __name__ == "__main__":
    confirm = input("This will DROP ALL DATA. Type 'yes' to continue: ")
    if confirm.strip().lower() == "yes":
        reset_db()
    else:
        print("Aborted.")