"""ASGI entry point for the private embedding service."""

from app.application import create_app

app = create_app()
