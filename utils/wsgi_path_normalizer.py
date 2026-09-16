"""WSGI middleware: fix PATH_INFO when proxy sends //clients/... (breaks Flask routing)."""


class DoubleSlashPathNormalizer:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        pi = environ.get("PATH_INFO") or ""
        if pi.startswith("//"):
            environ = environ.copy()
            rest = pi[2:].lstrip("/")
            environ["PATH_INFO"] = "/" + rest if rest else "/"
        return self.app(environ, start_response)
