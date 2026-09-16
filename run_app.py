#!/usr/bin/env python3
"""
Flask app runner with performance optimizations
"""
import os
from main import create_app
from config import config

# Set environment
env = os.environ.get('FLASK_ENV', 'development')
app = create_app(config[env])

if __name__ == '__main__':
    if env == 'test':
        print("Starting Flask app in TEST mode...")
        print("Press Ctrl+C to stop the server")
        app.run(host='127.0.0.1', port=5002, debug=True, threaded=True)
    elif env == 'development':
        print("Starting Flask app in development mode...")
        print("Press Ctrl+C to stop the server")
        app.run(host='127.0.0.1', port=5001, debug=True, threaded=True)
    else:
        print("Starting Flask app in production mode...")
        print("Press Ctrl+C to stop the server")
        app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)


