"""Entry point for development server.

Usage:
    python run.py
    python run.py --port=5001
"""

import argparse
from app import create_app

app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Flask development server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", default=True, help="Enable debug mode")
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=args.debug)
