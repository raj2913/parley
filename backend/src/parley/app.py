"""ASGI entry point: `uvicorn parley.app:app`."""

from parley.api import create_app

app = create_app()
