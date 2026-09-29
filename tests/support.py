"""Shared fixtures for cache tests."""

from __future__ import annotations

import struct
import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_png(width: int = 1, height: int = 1, rgb: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Build a valid uncompressed-filter PNG of a solid RGB color."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = (b"\x00" + bytes(rgb) * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@contextmanager
def serve_origin(
    body: bytes,
    content_type: str,
    *,
    path: str = "/pic.png",
    status: int = 200,
) -> Iterator[tuple[str, dict]]:
    """Serve one response and count GETs. Yields the origin URL and a hit counter."""
    state: dict = {"hits": 0, "user_agents": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state["hits"] += 1
            state["user_agents"].append(self.headers.get("User-Agent", ""))
            if self.path != path:
                payload = b"missing"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}{path}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
