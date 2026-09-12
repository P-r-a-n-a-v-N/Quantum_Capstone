"""Serving the built React app from the same process as the API.

In development the Vite dev server hosts the frontend on :5173 and proxies
/api and /ws to :8000. That is two processes, which is right for development
and wrong for deployment: nearly every free hosting tier gives you a single
service, and splitting this across two would mean paying for two.

Serving the built bundle from FastAPI collapses it to one container and one
origin, which also makes CORS and WebSocket-proxying non-issues in production.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for client-side routes.

    A single-page app owns its own routing, so a request for a path the server
    has never heard of is usually a deep link, not a mistake. Returning
    index.html lets React resolve it. Genuinely missing assets still 404,
    because only non-file paths fall through.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # A request for a real asset that is missing should stay a 404 --
            # silently serving HTML in place of a .js file produces a confusing
            # MIME-type error in the browser instead of an honest failure.
            if "." in Path(path).name:
                raise
            return FileResponse(Path(self.directory) / "index.html")


def mount_frontend(app: FastAPI, dist_dir: Path) -> bool:
    """Mount the built frontend at / if it exists. Returns whether it did.

    Must be called *after* the API routers are registered: routes are matched
    in registration order, so a catch-all mount added first would shadow /api
    and /ws.
    """
    index = dist_dir / "index.html"
    if not index.is_file():
        logger.info(
            "No built frontend at %s -- serving API only. "
            "Run `npm run build` in frontend/ to serve the UI from this process.",
            dist_dir,
        )
        return False

    app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="frontend")
    logger.info("Serving built frontend from %s", dist_dir)
    return True
