"""Command line entry for origin-image-cache."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from origin_image_cache import __version__
from origin_image_cache.cache import CacheError, ImageCache
from origin_image_cache.server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.command == "serve":
        return _serve(args.index, args.host, args.port)
    if args.command == "get":
        return _get(args.url, args.index, args.output)
    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="origin-image-cache",
        description="Serve image bytes for an origin URL from local indexes, or fetch and store a miss.",
    )
    parser.add_argument("--version", action="store_true", help="print the package version and exit")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser(
        "serve",
        help="serve cached image bytes over HTTP",
        description=(
            "Serve cached image bytes over HTTP. "
            "Binds to 127.0.0.1 by default and fetches whatever http(s) URL a client sends."
        ),
    )
    serve.add_argument("--index", type=Path, required=True, help="directory for the local image index")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")

    get = subparsers.add_parser("get", help="write image bytes for an origin URL")
    get.add_argument("url", help="origin image URL")
    get.add_argument("--index", type=Path, required=True, help="directory for the local image index")
    get.add_argument("-o", "--output", type=Path, help="write bytes to this file instead of stdout")
    return parser


def _serve(index: Path, host: str, port: int) -> int:
    cache = ImageCache(index)
    server = make_server(cache, host, port)
    bound_host, bound_port = server.server_address[:2]
    print(f"listening on http://{bound_host}:{bound_port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _get(url: str, index: Path, output: Path | None) -> int:
    try:
        image = ImageCache(index).get(url)
    except CacheError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    status = "HIT" if image.hit else "MISS"
    print(f"{status} {image.content_type} {len(image.body)} bytes", file=sys.stderr)
    if output is None:
        sys.stdout.buffer.write(image.body)
        return 0
    output.write_bytes(image.body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
