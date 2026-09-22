import logging

from flask import Blueprint

api_v1 = Blueprint("api_v1", __name__)
_log = logging.getLogger(__name__)


@api_v1.before_request
def _v1_require_auth():
    from api.v1.auth_guard import enforce_v1_api_auth

    return enforce_v1_api_auth()


def _safe_register(import_path: str, blueprint_name: str, url_prefix: str = ""):
    try:
        module = __import__(import_path, fromlist=[blueprint_name])
        bp = getattr(module, blueprint_name)
        api_v1.register_blueprint(bp, url_prefix=url_prefix)
    except Exception as e:
        # Keep API boot resilient in this repo's mixed state.
        _log.warning("api_v1: could not register %s.%s: %s", import_path, blueprint_name, e)


_safe_register("api.v1.period_analysis", "period_analysis_bp")
_safe_register("api.v1.performance", "performance_bp")
_safe_register("api.v1.cashflows", "cashflows_bp", url_prefix="/cashflows")
_safe_register("api.v1.cron_jobs", "cron_jobs_bp")
_safe_register("api.v1.whatsapp", "whatsapp_bp")
_safe_register("api.v1.enhanced_tax_optimiser", "enhanced_tax_optimiser_bp")
# Client-scoped APIs used by client details (timeline, transactions, status)
_safe_register("api.v1.performance_timeline", "performance_timeline_bp", url_prefix="/clients")
_safe_register("api.v1.transactions", "transactions_bp", url_prefix="/clients")
_safe_register("api.v1.clients", "clients_bp", url_prefix="/clients")
_safe_register("api.v1.price_accuracy", "price_accuracy_bp")
# Must pass url_prefix: _safe_register default "" overrides Blueprint(url_prefix="/public")
_safe_register("api.v1.public_contact", "public_contact_bp", url_prefix="/public")
_safe_register("api.v1.auth", "auth_api_bp", url_prefix="/auth")
_safe_register("api.v1.health", "health_bp")
_safe_register("api.v1.campaign_studio_api", "campaign_studio_api_bp", url_prefix="/campaign-studio")
# Pass url_prefix explicitly: _safe_register defaults to "" which overrides Blueprint(url_prefix=...).
_safe_register("api.v1.leegality", "leegality_bp", url_prefix="/leegality")
__all__ = ["api_v1"]
