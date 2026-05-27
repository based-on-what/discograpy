import logging
import os
import threading

from flask import Flask
from flask_cors import CORS

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
    CORS(app)

    from src.web.routes import bp
    app.register_blueprint(bp)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        from flask import jsonify
        app.logger.exception("Unhandled error: %s", error)
        return jsonify({"error": "Internal server error"}), 500

    return app
