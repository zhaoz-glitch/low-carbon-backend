"""Flask extensions (initialized in app factory, bound in __init__.py)."""

from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

db = SQLAlchemy()
cors = CORS()

try:
    from flask_migrate import Migrate

    migrate = Migrate()
except ImportError:  # pragma: no cover
    migrate = None
