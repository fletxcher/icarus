def main() -> None:
    """Runs the API locally with auto-reload: `uv run backend`."""
    import uvicorn

    uvicorn.run("backend.app:app", reload=True)
