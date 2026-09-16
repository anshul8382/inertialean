#!/usr/bin/env python3
"""
Production runner - Uses Gunicorn for production stability
This is the recommended way to run the app in production
"""
import os
import sys
import subprocess

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_BIN = os.path.join(APP_DIR, 'venv', 'bin')
GUNICORN_CONFIG = os.path.join(APP_DIR, 'config', 'gunicorn-inertiainvest.in.conf.py')

def main():
    # Ensure we're in the right directory
    os.chdir(APP_DIR)
    
    # Set environment
    os.environ['FLASK_ENV'] = 'production'
    
    # Build Gunicorn command
    gunicorn_cmd = [
        os.path.join(VENV_BIN, 'gunicorn'),
        '-c', GUNICORN_CONFIG,
        'wsgi:app'
    ]
    
    print("=" * 60)
    print("Starting Inertia Investment App in Production Mode")
    print("=" * 60)
    print(f"Working Directory: {APP_DIR}")
    print(f"Gunicorn Config: {GUNICORN_CONFIG}")
    print("=" * 60)
    print("\nThe app will run continuously and auto-restart on crashes.")
    print("Press Ctrl+C to stop the server\n")
    print("=" * 60)
    
    # Run Gunicorn
    try:
        subprocess.run(gunicorn_cmd, check=True)
    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        print(f"Error starting server: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()


