# Hermes Hub backend

FastAPI/Pydantic API with a Hub-owned SQLite database. It does not install or modify Hermes or Ollama, and it never reads their internal databases.

Use the repository-level `docs/setup.md` for installation and startup commands. Every startup must provide an exact `HUB_ALLOWED_ORIGINS` value; forwarded headers are not trusted and supplied Uvicorn commands disable proxy-header processing. Local snapshots are 0600 files in a 0700 directory and are bounded to the ten newest copies.
