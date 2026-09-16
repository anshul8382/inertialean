"""
App package entry point for production (e.g. passenger_wsgi.py).
Re-exports create_app from main so 'from app import create_app' uses the full application
with all blueprints and routes (including security-distribution, download_document, etc.).
"""
from main import create_app
from config import ProductionConfig

__all__ = ['create_app', 'ProductionConfig']
