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

        db.create_all()

        # Seed preset templates and mock data if empty
        from app.utils.mock_data import seed_mock_data
        seed_mock_data(db)

        # Seed a demo user if the users table is empty (dev convenience)
        from app.models.user import User
        if User.query.count() == 0:
            demo = User(email="demo@lowcarbon.io", name="Demo Investor")
            demo.set_password("demo123456")
            db.session.add(demo)
            db.session.commit()
            app.logger.info("Seeded demo user: demo@lowcarbon.io / demo123456")

    return app
