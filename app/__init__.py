"""Application factory pattern."""

from flask import Flask, jsonify
from .config import get_config
from .extensions import db, cors, migrate


def create_app(config_class=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config_class or get_config())

    # Initialize extensions
    db.init_app(app)
    if migrate is not None:
        migrate.init_app(app, db)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # Register blueprints
    from app.routes.screener import screener_bp
    from app.routes.stock import stock_bp
    from app.routes.auth import auth_bp
    from app.routes.db_admin import db_bp
    from app.routes.jobs import jobs_bp

    app.register_blueprint(screener_bp, url_prefix="/api")
    app.register_blueprint(stock_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(db_bp, url_prefix="/api")
    app.register_blueprint(jobs_bp, url_prefix="/api")

    # Health check
    @app.route("/health")
    def health():
        payload = {"status": "ok", "service": "low-carbon-screener-backend"}
        try:
            from app.jobs.sync import latest_sync

            payload["last_market_sync"] = latest_sync("market")
            payload["last_carbon_sync"] = latest_sync("carbon")
        except Exception:
            payload["last_market_sync"] = None
            payload["last_carbon_sync"] = None
        return jsonify(payload)

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
        from app.models import (  # noqa: F401
            company,
            financial_metric,
            carbon_emission,
            preset_template,
            user,
            data_sync_log,
            password_reset,
        )

        db.create_all()

        from app.utils.schema import ensure_schema
        from app.utils.mock_data import seed_demo_user, seed_mock_data
        from app.jobs.sync import seed_company_isins, recover_stale_jobs
        from app.services.tradingview_service import tradingview_service
        from app.services.carbon_service import carbon_service

        ensure_schema()
        seed_mock_data(db)
        seed_demo_user(db)
        seed_company_isins()
        recover_stale_jobs()
        tradingview_service.init_app(app)
        carbon_service.init_app(app)

    from app.jobs.scheduler import start_scheduler

    start_scheduler(app)

    return app
