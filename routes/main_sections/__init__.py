"""Register extracted main blueprint sections (preserves main.* endpoint names)."""
from . import assignments, pwa, reference_data, uploads, admin_tools


def register_all(main_bp):
    pwa.register(main_bp)
    reference_data.register(main_bp)
    uploads.register(main_bp)
    admin_tools.register(main_bp)
    assignments.register(main_bp)
