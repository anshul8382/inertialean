#!/usr/bin/env python3
"""
Flask app runner - imports create_app from main.py to avoid code duplication
"""
import os
from dotenv import load_dotenv
from main import create_app

if __name__ == '__main__':
    # Set environment from FLASK_ENV or default to production
    env = os.environ.get('FLASK_ENV', 'production')
    
    # Load environment-specific .env file
    env_file = f'.env.{env}'
    if os.path.exists(env_file):
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
    elif os.path.exists('.env'):
        load_dotenv('.env')
        print("Loaded environment from .env")
    
    # Import config based on environment
    from config import config
    config_class = config.get(env, config['default'])
    
    # Create app with appropriate config
    app = create_app(config_class)
    
    # Determine port based on environment
    if env == 'development':
        port = 5001
        print("Starting Flask app in development mode...")
        print("Press Ctrl+C to stop the server")
        app.run(host='127.0.0.1', port=port, debug=True, threaded=True)
    elif env == 'test':
        port = 5002
        print("Starting Flask app in test mode...")
        print("Press Ctrl+C to stop the server")
        app.run(host='127.0.0.1', port=port, debug=True, threaded=True)
    else:
        port = 5000
        print("Starting Flask app in production mode...")
        print("Press Ctrl+C to stop the server")
        app.run(host='127.0.0.1', port=port, debug=False, threaded=True) 