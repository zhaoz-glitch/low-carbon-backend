"""Application factory pattern."""

from flask import Flask, jsonify
from .config import get_config
from .extensions import db, migrate, cors


def create_app(config_class=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config_class or get_config())

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # Initialize data-source services (TradingView, Clarity AI/Bavest)
    from app.services.tradingview_service import tradingview_service
    from app.services.carbon_service import carbon_service

    tradingview_service.init_app(app)
    carbon_service.init_app(app)

    # Register blueprints
    from app.routes.screener import screener_bp
    from app.routes.stock import stock_bp
    from app.routes.auth import auth_bp
    from app.routes.db_admin import db_bp

    app.register_blueprint(screener_bp, url_prefix="/api")
    app.register_blueprint(stock_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(db_bp, url_prefix="/api")

    # Health check
    @app.route("/health")
    def health():
        return jsonify(status="ok", service="low-carbon-screener-backend")

    # Error handlers
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify(error="Bad request", message=str(e)), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify(error="Not found"), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify(error="Internal server error", message=str(e)), 500

    # Create tables (dev only; use migrations in production)
    with app.app_context():
        from app.models import (
            company,
            financial_metric,
            carbon_emission,
            preset_template,
            user,
        )  # noqa: F401

        # Log which database we are actually talking to
        from sqlalchemy import inspect as _insp
        import os as _os
        _raw_url = _os.environ.get("DATABASE_URL", "(unset)")
        # Dump *all* env vars whose name matches a DB-ish pattern, so we can
        # diagnose when Railway's reference variable wasn't injected.
        _dbish = {k: ("***" if "PASS" in k.upper() or "SECRET" in k.upper() else v)
                  for k, v in _os.environ.items()
                  if any(t in k.upper() for t in ("DATABASE", "MYSQL", "POSTGRES", "DB_", "RAILWAY_"))}
        print(f"[boot] env DATABASE_URL = {_raw_url}", flush=True)
        print(f"[boot] DB-ish env vars seen by gunicorn: {_dbish}", flush=True)
        try:
            _url = str(db.engine.url)
            _dialect = db.engine.url.get_backend_name()
            _host = db.engine.url.host
            _port = db.engine.url.port
            _dbname = db.engine.url.database
            _user = db.engine.url.username
            print(f"[boot] SQLAlchemy url (password masked) = {db.engine.url.render_as_string(hide_password=True)}", flush=True)
            print(f"[boot] DB connected -> dialect={_dialect} host={_host} port={_port} db={_dbname} user={_user}", flush=True)
        except Exception as _e:
            print(f"[boot] DB URL inspect failed: {_e}", flush=True)

        db.create_all()
        try:
            _post_tables = _insp(db.engine).get_table_names()
            print(f"[boot] create_all produced tables: {_post_tables}", flush=True)

            from app.utils.mock_data import seed_demo_user, seed_mock_data

            seed_mock_data(db)
            seed_demo_user(db)
            # Count via SQLAlchemy ORM core (cross-dialect) instead of raw SQL
            from sqlalchemy import func, select
            from app.models.company import Company
            from app.models.carbon_emission import CarbonEmission
            from app.models.financial_metric import FinancialMetric
            from app.models.preset_template import PresetTemplate
            from app.models.user import User
            _post_rows = {
                "companies":          db.session.scalar(select(func.count(Company.symbol))),
                "carbon_emissions":   db.session.scalar(select(func.count(CarbonEmission.id))),
                "financial_metrics":  db.session.scalar(select(func.count(FinancialMetric.symbol))),
                "preset_templates":   db.session.scalar(select(func.count(PresetTemplate.id))),
                "users":              db.session.scalar(select(func.count(User.id))),
            }
            print(f"[boot] post-seed row counts: {_post_rows}", flush=True)
        except Exception as _e:
            # Diagnostic block must not break the app
            print(f"[boot] post-seed diagnostic failed (non-fatal): {_e}", flush=True)

    return app
