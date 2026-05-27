import os

from dotenv import load_dotenv

load_dotenv()

from src.logging_config import configure_logging
from src.web import create_app

configure_logging(verbose=False)

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
