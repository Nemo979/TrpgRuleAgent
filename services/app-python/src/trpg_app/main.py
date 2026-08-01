import logging
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_config


def run() -> None:
    logging.getLogger().setLevel(logging.INFO)
    config = load_config()
    static_dir = Path("apps/web-next/dist")
    host = os.environ.get("TRPG_HOST", "127.0.0.1")
    port = int(os.environ.get("TRPG_PORT", "8000"))
    uvicorn.run(create_app(config, static_dir), host=host, port=port)


if __name__ == "__main__":
    run()
