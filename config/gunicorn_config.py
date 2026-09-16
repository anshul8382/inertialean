# Gunicorn configuration file for production
import multiprocessing
import os

# Server socket
bind = "0.0.0.0:5000"
backlog = 2048

# Worker processes - optimized for 2-core system
# For 2 cores, use 2-3 workers max to prevent resource contention
cpu_count = multiprocessing.cpu_count()
if cpu_count <= 2:
    workers = 2  # Conservative for small systems
else:
    workers = min(cpu_count * 2 + 1, 4)  # Cap at 4 workers max

worker_class = "sync"
worker_connections = 1000

# Request limits to prevent memory leaks and timeouts
max_requests = 500  # Reduced from 1000
max_requests_jitter = 25  # Reduced from 50

# Timeout settings — hybrid Claude reports; align with Apache/nginx (see config/inertiainvest.in.conf)
timeout = 300
keepalive = 5
graceful_timeout = 30

# Memory management
max_requests_jitter = 25
worker_tmp_dir = "/dev/shm"  # Use RAM for temporary files

# Preload app for better performance
preload_app = True

# Logging
accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "inertia_app"

# Server mechanics
daemon = False
pidfile = "gunicorn.pid"
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
# keyfile = "path/to/keyfile"
# certfile = "path/to/certfile"

# Server hooks
def on_starting(server):
    server.log.info("Starting Inertia Investment App")

def on_reload(server):
    server.log.info("Reloading Inertia Investment App")

def worker_int(worker):
    worker.log.info("worker received INT or QUIT signal")

def pre_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def post_worker_init(worker):
    worker.log.info("Worker initialized (pid: %s)", worker.pid)

def worker_abort(worker):
    worker.log.info("Worker aborted (pid: %s)", worker.pid)

def worker_exit(server, worker):
    server.log.info("Worker exited (pid: %s)", worker.pid) 