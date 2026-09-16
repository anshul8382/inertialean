import multiprocessing
import os

# Server socket
bind = "0.0.0.0:5000"
backlog = 2048

# Worker processes - Single worker for testing
workers = 1  # Single worker for testing
worker_class = 'sync'
worker_connections = 1000
timeout = 300
keepalive = 2

# Logging
accesslog = os.path.join(os.path.dirname(__file__), 'logs/access.log')
errorlog = os.path.join(os.path.dirname(__file__), 'logs/error.log')
loglevel = 'info'

# Process naming
proc_name = 'gunicorn_portfolio_single'

# SSL
keyfile = None
certfile = None

# Server mechanics
daemon = False
pidfile = os.path.join(os.path.dirname(__file__), 'gunicorn_single.pid')
umask = 0
user = None
group = None
tmp_upload_dir = None

# Server hooks
def on_starting(server):
    pass

def on_reload(server):
    pass

def on_exit(server):
    pass


