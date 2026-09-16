import sys
import os

# Add the app directory to Python path
sys.path.insert(0, '/home/inertia/app')

# Import the Flask app
from app import create_app

# Create the application instance
application = create_app()

# For debugging
if __name__ == '__main__':
    application.run() 