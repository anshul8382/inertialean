#!/usr/bin/env python3
"""
Script to create the cron_schedule table in the database
"""

import sys
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# Add current directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Database configuration from wsgi.py
DB_USER = os.environ.get('DB_USER', 'inertia_admin')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_NAME = os.environ.get('DB_NAME', 'inertia_app2025')
DATABASE_URL = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:3306/{DB_NAME}'

Base = declarative_base()

class CronSchedule(Base):
    __tablename__ = 'cron_schedule'
    id = Column(Integer, primary_key=True)
    job_id = Column(String(100), nullable=False, unique=True)
    schedule = Column(String(100), nullable=False)  # Cron expression
    description = Column(String(200))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<CronSchedule {self.job_id}: {self.schedule}>'

def create_cron_schedule_table():
    print("🚀 Creating CronSchedule table...")
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Check if table exists
        import sqlalchemy as sa
        inspector = sa.inspect(engine)
        if inspector.has_table(CronSchedule.__tablename__):
            print(f"Table '{CronSchedule.__tablename__}' already exists. Skipping creation.")
        else:
            print(f"Creating {CronSchedule.__tablename__} table...")
            Base.metadata.create_all(engine)
            print(f"✅ {CronSchedule.__tablename__} table created successfully!")

        # Verify table creation
        if inspector.has_table(CronSchedule.__tablename__):
            print("✅ Table verification successful")
        else:
            print("❌ Table verification failed")

        session.commit()
        print("🎉 CronSchedule table creation completed successfully!")
    except Exception as e:
        session.rollback()
        print(f"❌ Error creating CronSchedule table: {e}")
    finally:
        session.close()

if __name__ == '__main__':
    create_cron_schedule_table()
