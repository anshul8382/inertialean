#!/usr/bin/env python3
"""
WSGI entry point for production deployment with Gunicorn
This file is used by Gunicorn to serve the Flask application
"""
import sys
import os

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = os.path.join(current_dir, '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded environment variables from {env_path}")
    else:
        print(f"Warning: .env file not found at {env_path}")
except ImportError:
    print("Warning: python-dotenv not installed. Environment variables must be set manually.")

# Set environment to production
os.environ['FLASK_ENV'] = 'production'
os.environ['FLASK_APP'] = 'main.py'

# Import and create the application
from main import create_app
from config import ProductionConfig

# Create the application instance
app = create_app(ProductionConfig)

# Add teardown handler to clean up database connections (additional safety)
@app.teardown_appcontext
def close_db(error):
    """Clean up database connections after each request"""
    from extensions import db
    try:
        if error:
            db.session.rollback()
        db.session.remove()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error cleaning up database session: {str(e)}")

# For Gunicorn
if __name__ == "__main__":
    # This should not run in production (Gunicorn handles it)
    # But useful for testing
    app.run(host='0.0.0.0', port=5000, debug=False)
