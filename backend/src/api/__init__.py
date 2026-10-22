def main() -> None:
    """Runs the API locally with auto-reload: `uv run api`."""
    import uvicorn

    uvicorn.run("api.app:app", reload=True)
