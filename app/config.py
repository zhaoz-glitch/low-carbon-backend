"""Application configuration.

Database selection is driven by the ``DATABASE_URL`` environment variable:

* ``DATABASE_URL`` unset / empty  → SQLite at ``instance/low_carbon_screener.db``
                                      (used for local development)
* ``DATABASE_URL=mysql://...``     → MySQL via ``pymysql`` (used by the Railway
                                      MySQL plugin).  The scheme is rewritten to
                                      ``mysql+pymysql://`` so SQLAlchemy picks up
                                      the pure-Python driver and we don't need
                                      to ship a C compiler on the image.

Postgres, MS SQL, etc. work too — only MySQL needs the explicit driver prefix
because PyMySQL doesn't register the ``mysql://`` URI itself.
"""

import os
import urllib.parse

from dotenv import load_dotenv

load_dotenv()


def _build_database_uri() -> str:
    """Return a SQLAlchemy-compatible database URI.

    Resolution order:

    1. ``DATABASE_URL``  (Heroku / Railway / Render convention; takes
       precedence because operators usually set this on purpose to
       override the default).
    2. ``MYSQL_URL``     (auto-injected by the Railway MySQL plugin into
       every service in the same project — no manual reference variable
       required).
    3. SQLite at ``instance/low_carbon_screener.db`` for local dev.

    The ``mysql://`` scheme is rewritten to ``mysql+pymysql://`` so
    SQLAlchemy picks up the pure-Python driver and we don't need to ship
    a C compiler on the image.
    """
    raw = os.environ.get("DATABASE_URL") or os.environ.get("MYSQL_URL")
    if not raw:
        return "sqlite:///low_carbon_screener.db"

    # Older SQLAlchemy used 'postgres://' — normalise to 'postgresql://'
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]

    if raw.startswith("mysql://"):
        # mysqlclient is faster but needs libmysqlclient-dev on the image.
        # PyMySQL is pure-Python and works out of the box on slim Railway images.
        raw = "mysql+pymysql://" + raw[len("mysql://"):]

    return raw


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # Database
    SQLALCHEMY_DATABASE_URI = _build_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_pre_ping avoids "MySQL server has gone away" errors when the
    # upstream pool recycles idle connections; pool_recycle must stay below
    # MySQL's ``wait_timeout`` (default 8h, but Railway can be more aggressive).
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }

    # Redis
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CACHE_TTL = int(os.environ.get("CACHE_TTL", 300))

    # External APIs
    TRADINGVIEW_ENABLED = os.environ.get("TRADINGVIEW_ENABLED", "true").lower() == "true"

    # Clarity AI (primary carbon data provider) — https://developer.clarity.ai
    # Get Client Key / Secret from Developer Settings in the Clarity AI app.
    CLARITY_AI_KEY = os.environ.get("CLARITY_AI_KEY", "")
    CLARITY_AI_SECRET = os.environ.get("CLARITY_AI_SECRET", "")
    CLARITY_AI_BASE_URL = os.environ.get(
        "CLARITY_AI_BASE_URL", "https://api.clarity.ai/clarity/v1"
    )
    # Optional extra ticker→ISIN overrides as a JSON object string, e.g.
    # '{"SPGI": "US78409V1020"}' — merged over the built-in static map.
    ISIN_MAP_JSON = os.environ.get("ISIN_MAP_JSON", "")

    # Climatiq (spend-based carbon estimation provider) — https://www.climatiq.io
    # Get the API key from the Climatiq dashboard (Account → API Keys).
    CLIMATIQ_API_KEY = os.environ.get("CLIMATIQ_API_KEY", "")
    CLIMATIQ_BASE_URL = os.environ.get(
        "CLIMATIQ_BASE_URL", "https://api.climatiq.io"
    )

    # Bavest (secondary carbon provider) & Intrinio (legacy)
    BAVEST_API_KEY = os.environ.get("BAVEST_API_KEY", "")
    BAVEST_BASE_URL = os.environ.get("BAVEST_BASE_URL", "https://api.bavest.co")
    INTRINIO_API_KEY = os.environ.get("INTRINIO_API_KEY", "")

    # Pagination
    DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", 50))
    MAX_PAGE_SIZE = int(os.environ.get("MAX_PAGE_SIZE", 200))


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    ENV = "development"


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    ENV = "production"


class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DEBUG = True


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    """Return the config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
