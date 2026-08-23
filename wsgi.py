"""WSGI entry point for production (gunicorn / uwsgi)."""

from app import create_app

app = create_app()
