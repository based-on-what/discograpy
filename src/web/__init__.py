import logging
import os
import threading

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from src.web.limiter import limiter

_client = None
_service = None
_lock = threading.Lock()


def get_client():
    from src.config import build_spotify_client
    from src.services.spotify_client import SpotifyClient

    global _client
    if _client is None:
        with _lock:
            if _client is None:
                sp = build_spotify_client()
                _client = SpotifyClient(sp=sp, logger=logging.getLogger("spotify_client"))
    return _client


def get_service():
    from src.services.discography import DiscographyService

    global _service
    if _service is None:
        with _lock:
            if _service is None:
                _service = DiscographyService(
                    client=get_client(),
                    logger=logging.getLogger("discography"),
                )
    return _service


def create_app() -> Flask:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    app = Flask(
        __name__,
        template_folder=os.path.join(_root, "templates"),
    )
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "discograpy-dev-secret")

    # Railway terminates TLS in front of gunicorn, so request.remote_addr is the
    # proxy without this and every visitor would share one rate-limit bucket.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # No CORS: the frontend is served by this same app and calls the API with
    # relative paths, so no cross-origin caller should reach these routes.
    limiter.init_app(app)

    from src.web.routes import bp
    app.register_blueprint(bp)

    @app.errorhandler(429)
    def handle_rate_limit(error):
        from flask import jsonify
        return (
            jsonify({"error": f"Too many requests — {error.description}. Wait and try again."}),
            429,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        from flask import jsonify
        app.logger.exception("Unhandled error: %s", error)
        return jsonify({"error": "Internal server error"}), 500

    return app
