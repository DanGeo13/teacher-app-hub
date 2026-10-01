from .api import create_app
from .settings import Settings


def create_app_from_env():
    """Uvicorn factory; validates environment before opening the database."""
    return create_app(Settings.from_env())
