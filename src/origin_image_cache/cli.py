"""Command line entry for origin-image-cache."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from origin_image_cache import __version__
from origin_image_cache.cache import CacheError, ImageCache, ReadOnlyIndex, cache_status
from origin_image_cache.server import make_server

_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_REQUIRED_KEYS = ("db", "table", "root", "urlColumn", "pathColumn")
_OPTIONAL_KEYS = ("statusColumn", "status")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.command == "serve":
        return _serve(args.index, args.host, args.port, args.read_only, args.max_bytes)
    if args.command == "get":
        return _get(args.url, args.index, args.output, args.read_only, args.max_bytes)
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
    _add_cache_args(serve)
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")

    get = subparsers.add_parser("get", help="write image bytes for an origin URL")
    get.add_argument("url", help="origin image URL")
    _add_cache_args(get)
    get.add_argument("-o", "--output", type=Path, help="write bytes to this file instead of stdout")
    return parser


def _add_cache_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index", type=Path, required=True, help="directory for the local image index")
    parser.add_argument("--read-only", type=Path, help="JSON file listing read-only sqlite indexes")
    parser.add_argument(
        "--max-bytes",
        type=_positive_int,
        default=_DEFAULT_MAX_BYTES,
        help=f"largest image to accept, in bytes (default: {_DEFAULT_MAX_BYTES})",
    )


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max-bytes must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("max-bytes must be a positive integer")
    return number


def _serve(index: Path, host: str, port: int, read_only: Path | None, max_bytes: int) -> int:
    try:
        cache = _open_cache(index, read_only, max_bytes)
    except CacheError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    server = make_server(cache, host, port)
    bound_host, bound_port = server.server_address[:2]
    print(f"listening on http://{bound_host}:{bound_port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        cache.close()
    return 0


def _get(url: str, index: Path, output: Path | None, read_only: Path | None, max_bytes: int) -> int:
    try:
        with _open_cache(index, read_only, max_bytes) as cache:
            image = cache.get(url)
    except CacheError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{cache_status(image.where)} {image.content_type} {len(image.body)} bytes", file=sys.stderr)
    if output is None:
        sys.stdout.buffer.write(image.body)
        return 0
    output.write_bytes(image.body)
    return 0


def _open_cache(index: Path, read_only: Path | None, max_bytes: int) -> ImageCache:
    return ImageCache(index, max_bytes=max_bytes, read_only=_load_read_only(read_only))


def _load_read_only(path: Path | None) -> list[ReadOnlyIndex]:
    if path is None:
        return []
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CacheError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CacheError(f"read-only config is not valid JSON: {path}") from exc
    if isinstance(data, dict):
        entries = [data]
    elif isinstance(data, list):
        entries = data
    else:
        raise CacheError(f"read-only config must be a JSON object or array: {path}")
    return [_read_only_entry(entry, path.parent) for entry in entries]


def _read_only_entry(entry: object, base: Path) -> ReadOnlyIndex:
    if not isinstance(entry, dict):
        raise CacheError("read-only config entries must be JSON objects")
    allowed = set(_REQUIRED_KEYS) | set(_OPTIONAL_KEYS)
    unknown = sorted(set(entry) - allowed)
    missing = sorted(key for key in _REQUIRED_KEYS if key not in entry)
    if unknown:
        raise CacheError(f"read-only config has unknown keys: {', '.join(unknown)}")
    if missing:
        raise CacheError(f"read-only config is missing keys: {', '.join(missing)}")
    has_column = "statusColumn" in entry
    has_status = "status" in entry
    if has_column != has_status:
        raise CacheError("read-only config statusColumn and status must both be set")
    status_column = _require_name(entry["statusColumn"], "statusColumn") if has_column else None
    status = _require_text(entry["status"], "status") if has_status else None
    return ReadOnlyIndex(
        db=_resolve_config_path(entry["db"], base, "db"),
        table=_require_name(entry["table"], "table"),
        root=_resolve_config_path(entry["root"], base, "root"),
        url_column=_require_name(entry["urlColumn"], "urlColumn"),
        path_column=_require_name(entry["pathColumn"], "pathColumn"),
        status_column=status_column,
        status=status,
    )


def _require_name(value: object, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise CacheError(f"read-only config {label} must be a non-empty string")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise CacheError(f"read-only config {label} must be a string")
    return value


def _resolve_config_path(value: object, base: Path, label: str) -> Path:
    path = Path(_require_name(value, label))
    if not path.is_absolute():
        path = base / path
    return path


if __name__ == "__main__":
    raise SystemExit(main())
