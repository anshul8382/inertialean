# Gunicorn configuration file for Inertia Investment App
import multiprocessing
import os

# Server socket
bind = "0.0.0.0:5000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
# Hybrid Claude reports can run several minutes; align with reverse-proxy timeouts (see config/inertiainvest.in.conf)
timeout = 300
keepalive = 2

# Restart workers after this many requests, to help prevent memory leaks
max_requests = 1000
max_requests_jitter = 50

# Logging
# Use stderr so journalctl captures logs (avoids permission issues with log files)
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = 'inertia_app'

# Server mechanics
daemon = False
pidfile = '/home/inertia/app/gunicorn.pid'
user = 'root'
group = 'root'
tmp_upload_dir = None

# SSL (if needed)
# keyfile = '/path/to/keyfile'
# certfile = '/path/to/certfile'

# Environment variables
raw_env = [
    'FLASK_ENV=production',
    'FLASK_APP=main.py',
]

# Preload app for better performance
preload_app = True

# Worker timeout
graceful_timeout = 30

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
