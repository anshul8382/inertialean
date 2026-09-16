# Gunicorn Configuration for inertiainvest.in
# Run with: gunicorn -c gunicorn-inertiainvest.in.conf.py wsgi:app

import multiprocessing
import os

# Server socket
bind = "127.0.0.1:8000"
backlog = 2048

# Worker processes - optimized for stability
cpu_count = multiprocessing.cpu_count()
if cpu_count <= 2:
    workers = 2  # Conservative for small systems
else:
    workers = min(cpu_count * 2 + 1, 8)  # Cap at 8 workers max

worker_class = "sync"
worker_connections = 1000

# Timeout settings — hybrid Claude reports; align with Apache ProxyTimeout / nginx proxy_read_timeout
timeout = 300
graceful_timeout = 30  # Time to wait for workers to finish
keepalive = 5

# Restart workers after this many requests, to help prevent memory leaks
max_requests = 1000
max_requests_jitter = 50

# Memory management - restart workers if they use too much memory
worker_tmp_dir = "/dev/shm"  # Use RAM for temp files if available

# Logging
accesslog = "/home/inertia/app/logs/gunicorn_access.log"
errorlog = "/home/inertia/app/logs/gunicorn_error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "inertia_app"

# Server mechanics
daemon = False  # systemd handles daemonization
pidfile = "/home/inertia/app/gunicorn.pid"
user = "inertia"
group = "inertia"
tmp_upload_dir = None

# Worker lifecycle hooks for better error handling
def worker_int(worker):
    """Called when worker receives INT or QUIT signal"""
    worker.log.info("Worker received INT or QUIT signal")

def worker_abort(worker):
    """Called when worker times out"""
    worker.log.error("Worker aborted due to timeout")

def pre_fork(server, worker):
    """Called just before a worker is forked"""
    server.log.info("Worker about to be spawned")

def post_fork(server, worker):
    """Called just after a worker has been forked"""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def post_worker_init(worker):
    """Called just after a worker has initialized"""
    worker.log.info("Worker initialized (pid: %s)", worker.pid)

def worker_exit(server, worker):
    """Called when a worker exits"""
    server.log.info("Worker exited (pid: %s)", worker.pid)

def on_exit(server):
    """Called just before exiting"""
    server.log.info("Server shutting down")

# SSL (if running directly with Gunicorn)
# keyfile = "/etc/ssl/private/inertiainvest.in.key"
# certfile = "/etc/ssl/certs/inertiainvest.in.crt"

# Environment variables
raw_env = [
    'FLASK_ENV=production',
    'SERVER_NAME=inertiainvest.in',
]

# Preload app for better performance
preload_app = True

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
