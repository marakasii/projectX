"""Serve an independently built frontend on the API origin, never demo data."""

from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Unknown API/private URLs must never receive a successful SPA page.
        if path.split("/", 1)[0] in {"api", "local_data", ".env", ".git", ".runtime", "docs", "redoc", "openapi.json"}:
            raise HTTPException(404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix or ".." in Path(path).parts:
                raise
            return FileResponse(Path(self.directory) / "index.html", headers={"Cache-Control": "no-cache"})


def install_web(application, settings):
    directory = settings.frontend_dist
    if directory is None:
        candidate = Path("frontend/dist")
        directory = candidate if (candidate / "index.html").is_file() else None
    if directory is not None:
        directory = directory.resolve()
        private = settings.data_dir.resolve()
        if directory.is_relative_to(private) or private.is_relative_to(directory):
            raise ValueError("Frontend directory must be separate from private data")
        if not (directory / "index.html").is_file():
            raise ValueError("Frontend build needs index.html")
        if not (directory / "index.html").resolve().is_relative_to(directory):
            raise ValueError("Frontend index must remain inside the public build")
        application.mount("/", FrontendFiles(directory=directory, html=True), name="frontend")
    else:
        @application.get("/", include_in_schema=False)
        def web_entry():
            return RedirectResponse("/docs", status_code=307)
