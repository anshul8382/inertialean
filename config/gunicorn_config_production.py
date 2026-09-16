# Gunicorn configuration file for production - optimized version
import multiprocessing
import os

# Server socket
bind = "0.0.0.0:5000"
backlog = 2048

# Worker processes - optimized for 2-core system
cpu_count = multiprocessing.cpu_count()
if cpu_count <= 2:
    workers = 2  # Conservative for small systems
else:
    workers = min(cpu_count * 2 + 1, 4)  # Cap at 4 workers max

worker_class = "sync"
worker_connections = 1000

# Request limits to prevent memory leaks and timeouts
max_requests = 300  # Even more conservative for production
max_requests_jitter = 20

# Timeout settings — must exceed worst-case Claude + JSON (see ProxyTimeout / nginx proxy_read_timeout)
timeout = 300
keepalive = 5
graceful_timeout = 30

# Memory management
worker_tmp_dir = "/dev/shm"  # Use RAM for temporary files

# Preload app for better performance
preload_app = True

# Logging with rotation
accesslog = "/home/inertia/app/logs/gunicorn_access.log"
errorlog = "/home/inertia/app/logs/gunicorn_error.log"
loglevel = "warning"  # Reduce log verbosity in production
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "inertia_app_prod"

# Server mechanics
daemon = False  # systemd handles daemonization
pidfile = "/home/inertia/app/gunicorn.pid"
user = None
group = None
tmp_upload_dir = None

# Security settings
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

# Server hooks
def on_starting(server):
    server.log.info("Starting Inertia Investment App (Production)")

def on_reload(server):
    server.log.info("Reloading Inertia Investment App (Production)")

def worker_int(worker):
    worker.log.info("worker received INT or QUIT signal")

def pre_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def post_fork(server, worker):
    server.log.info("Worker forked (pid: %s)", worker.pid)
    # preload_app=True loads the app in the master; forked workers inherit the same
    # SQLAlchemy pool / MySQL sockets. Using them causes PyMySQL
    # "Packet sequence number wrong" and similar errors. Dispose so each worker opens
    # its own connections.
    try:
        from wsgi import app as flask_app
        from extensions import db

        with flask_app.app_context():
            db.engine.dispose()
        worker.log.info("SQLAlchemy engine disposed for worker %s (post-fork pool reset)", worker.pid)
    except Exception as e:
        worker.log.warning("post_fork: could not dispose SQLAlchemy engine: %s", e)

def post_worker_init(worker):
    worker.log.info("Worker initialized (pid: %s)", worker.pid)
    # Database connection will be verified on first request

def worker_abort(worker):
    worker.log.info("Worker aborted (pid: %s)", worker.pid)

def worker_exit(server, worker):
    server.log.info("Worker exited (pid: %s)", worker.pid)
    # Flask-SQLAlchemy session.remove() requires an application context (not set in Gunicorn hooks).
    try:
        from extensions import db
        from wsgi import app as flask_app

        with flask_app.app_context():
            db.session.remove()
        worker.log.info("Database session cleaned up for worker %s", worker.pid)
    except Exception as e:
        worker.log.error("Error cleaning up database session for worker %s: %s", worker.pid, str(e))

def when_ready(server):
    server.log.info("Server is ready. Spawning workers")

def on_exit(server):
    server.log.info("Server is shutting down")
