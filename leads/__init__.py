from flask import Blueprint

leads = Blueprint('leads', __name__)

from . import views 