# Gunicorn for parallel soak-test instance (Apache :5003 SSL → this on 127.0.0.1:5004).
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:5004")
workers = max(2, multiprocessing.cpu_count() // 2)
worker_class = "sync"
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
