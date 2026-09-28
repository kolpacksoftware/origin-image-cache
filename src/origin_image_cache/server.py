"""HTTP server that returns image bytes for an origin URL."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from origin_image_cache.cache import CacheError, ImageCache


class ImageCacheServer(ThreadingHTTPServer):
    """Threading HTTP server that reuses its address across local restarts."""

    allow_reuse_address = True
    daemon_threads = True


def make_server(
    cache: ImageCache,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    quiet: bool = False,
) -> ImageCacheServer:
    """Bind an HTTP server that serves `cache`."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            route = parsed.path.rstrip("/") or "/"
            if route == "/health":
                self._send_json(200, {"status": "ok"})
                return
            if route != "/v1/image":
                self._send_json(404, {"error": "not found"})
                return
            origin_url = _query_value(parsed.query, "url")
            if origin_url is None:
                self._send_json(400, {"error": "missing url query parameter"})
                return
            try:
                image = cache.get(origin_url)
            except CacheError as exc:
                self._send_json(exc.status, {"error": str(exc)})
                return
            self._send_bytes(
                200,
                image.body,
                image.content_type,
                extra=[("X-Cache", "HIT" if image.hit else "MISS")],
            )

        def log_message(self, fmt: str, *args: object) -> None:
            if not quiet:
                super().log_message(fmt, *args)

        def _send_bytes(
            self,
            status: int,
            body: bytes,
            content_type: str,
            extra: list[tuple[str, str]] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for name, value in extra or []:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = (json.dumps(payload) + "\n").encode("utf-8")
            self._send_bytes(status, body, "application/json")

    return ImageCacheServer((host, port), Handler)


def _query_value(query: str, name: str) -> str | None:
    """Read one query value without treating '+' as a space."""
    prefix = f"{name}="
    for part in query.split("&"):
        if part.startswith(prefix):
            return unquote(part[len(prefix) :])
    return None
