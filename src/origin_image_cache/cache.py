"""Look up image bytes by origin URL, fetching and storing a miss."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STATUS = {"index": "HIT", "read-only": "INDEX", "miss": "MISS"}


class CacheError(Exception):
    """Base error for index lookup and origin fetch."""

    status = 500


class InvalidUrlError(CacheError):
    """The client did not supply an http(s) URL."""

    status = 400


class NotAnImageError(CacheError):
    """The origin response is not a recognized image."""

    status = 415


class TooLargeError(CacheError):
    """The origin image is larger than the configured limit."""

    status = 413


class OriginNotFoundError(CacheError):
    """The origin responded 404."""

    status = 404


class OriginError(CacheError):
    """The origin could not be fetched."""

    status = 502


@dataclass(frozen=True)
class CachedImage:
    """Image bytes for one origin URL."""

    url: str
    body: bytes
    content_type: str
    hit: bool
    where: str


@dataclass(frozen=True)
class ReadOnlyIndex:
    """A sqlite database queried before an origin fetch, never written.

    ``status_column`` and ``status`` are optional and must be set together.
    Table and column names must be plain identifiers; they are quoted into
    SQL, and the URL and status values stay bound parameters.
    """

    db: Path
    table: str
    root: Path
    url_column: str
    path_column: str
    status_column: str | None = None
    status: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "db", Path(self.db))
        object.__setattr__(self, "root", Path(self.root))


@dataclass
class _OpenReadOnly:
    root: Path
    sql: str
    status: str | None
    connection: sqlite3.Connection


def cache_status(where: str) -> str:
    """Return the HIT, INDEX, or MISS token for a ``CachedImage.where`` value."""
    try:
        return _STATUS[where]
    except KeyError:
        raise CacheError(f"unknown cache where: {where}") from None


class ImageCache:
    """Local index of origin image URLs.

    A hit reads bytes already stored for that URL. A read-only hit reads a
    file named by another sqlite index without copying it. A miss fetches the
    URL, checks that the body is an image, and stores it.
    """

    def __init__(
        self,
        index_dir: str | Path,
        *,
        timeout: float = 20.0,
        max_bytes: int = 25 * 1024 * 1024,
        read_only: Sequence[ReadOnlyIndex] | None = None,
    ) -> None:
        self.index_dir = Path(index_dir)
        self.objects_dir = self.index_dir / "objects"
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._db: sqlite3.Connection | None = None
        self._read_only: list[_OpenReadOnly] = []
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        try:
            # serve calls get on worker threads. The lock in get serializes this connection.
            self._db = sqlite3.connect(self.index_dir / "index.sqlite", check_same_thread=False)
            with self._db:
                self._db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS objects (
                        url TEXT PRIMARY KEY,
                        path TEXT NOT NULL,
                        content_type TEXT NOT NULL
                    )
                    """
                )
            for spec in read_only or ():
                self._read_only.append(self._open_read_only(spec))
        except CacheError:
            self.close()
            raise
        except sqlite3.Error as exc:
            self.close()
            raise CacheError(f"failed to open index {self.index_dir / 'index.sqlite'}: {exc}") from exc

    def close(self) -> None:
        """Close the writable index and any read-only databases."""
        for opened in self._read_only:
            opened.connection.close()
        self._read_only.clear()
        if self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> ImageCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str) -> CachedImage:
        """Return image bytes for `url`, fetching and storing them on a miss."""
        normalized = normalize_url(url)
        with self._lock:
            cached = self._lookup(normalized)
            if cached is not None:
                return cached
            external = self._lookup_read_only(normalized)
            if external is not None:
                return external
            body, content_type = self._fetch(normalized)
            self._store(normalized, body, content_type)
            return CachedImage(
                url=normalized,
                body=body,
                content_type=content_type,
                hit=False,
                where="miss",
            )

    def _lookup(self, url: str) -> CachedImage | None:
        try:
            row = self._require_db().execute(
                "SELECT path, content_type FROM objects WHERE url = ?",
                (url,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise CacheError(f"index lookup failed: {exc}") from exc
        if row is None:
            return None
        relative, content_type = row
        path = self._object_path(relative)
        if not path.is_file():
            return None
        return CachedImage(
            url=url,
            body=path.read_bytes(),
            content_type=content_type,
            hit=True,
            where="index",
        )

    def _lookup_read_only(self, url: str) -> CachedImage | None:
        twin = _scheme_twin(url)
        for opened in self._read_only:
            for candidate in (url, twin):
                found = self._match_read_only(opened, candidate, url)
                if found is not None:
                    return found
        return None

    def _match_read_only(self, opened: _OpenReadOnly, candidate: str, requested: str) -> CachedImage | None:
        params: tuple[str, ...] = (candidate,) if opened.status is None else (candidate, opened.status)
        try:
            rows = opened.connection.execute(opened.sql, params).fetchall()
        except sqlite3.Error as exc:
            raise CacheError(f"read-only index query failed: {exc}") from exc
        for (stored,) in rows:
            image = self._image_from_read_only_file(opened.root, stored, requested)
            if image is not None:
                return image
        return None

    def _image_from_read_only_file(self, root: Path, stored: object, url: str) -> CachedImage | None:
        path = _readable_in_root(root, stored)
        if path is None:
            return None
        body = self._read_limited(path, url)
        if body is None:
            return None
        content_type = _stored_content_type(body)
        if content_type is None:
            return None
        return CachedImage(url=url, body=body, content_type=content_type, hit=True, where="read-only")

    def _read_limited(self, path: Path, url: str) -> bytes | None:
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size > self.max_bytes:
            raise TooLargeError(f"image at {url} exceeds {self.max_bytes} bytes")
        try:
            body = path.read_bytes()
        except OSError:
            return None
        if len(body) > self.max_bytes:
            raise TooLargeError(f"image at {url} exceeds {self.max_bytes} bytes")
        return body

    def _fetch(self, url: str) -> tuple[bytes, str]:
        request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return self._read_image(url, response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise OriginNotFoundError(f"origin returned 404 for {url}") from exc
            raise OriginError(f"origin returned {exc.code} for {url}") from exc
        except TimeoutError as exc:
            raise OriginError(f"timed out fetching {url}") from exc
        except urllib.error.URLError as exc:
            raise OriginError(f"failed to fetch {url}: {exc.reason}") from exc

    def _read_image(self, url: str, response: HTTPResponse) -> tuple[bytes, str]:
        headers = response.headers
        raw_length = headers.get("Content-Length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    raise TooLargeError(f"image at {url} exceeds {self.max_bytes} bytes")
            except ValueError:
                pass
        body = response.read(self.max_bytes + 1)
        if len(body) > self.max_bytes:
            raise TooLargeError(f"image at {url} exceeds {self.max_bytes} bytes")
        header_type = ""
        if headers.get("Content-Type"):
            header_type = headers.get_content_type()
        content_type = resolve_content_type(header_type, body)
        return body, content_type

    def _store(self, url: str, body: bytes, content_type: str) -> None:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        relative = f"{digest[:2]}/{digest}"
        destination = self.objects_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, destination)
        try:
            with self._require_db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO objects (url, path, content_type) VALUES (?, ?, ?)",
                    (url, relative, content_type),
                )
        except sqlite3.Error as exc:
            raise CacheError(f"failed to store index row for {url}") from exc

    def _open_read_only(self, spec: ReadOnlyIndex) -> _OpenReadOnly:
        if (spec.status_column is None) != (spec.status is None):
            raise CacheError("statusColumn and status must both be set")
        sql = _read_only_sql(spec)
        db_path = Path(spec.db)
        uri = db_path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.Error as exc:
            raise CacheError(f"read-only index is not readable: {db_path}") from exc
        probe: tuple[str, ...] = ("",) if spec.status is None else ("", spec.status)
        try:
            connection.execute(sql + " LIMIT 0", probe)
        except sqlite3.Error as exc:
            connection.close()
            raise CacheError(f"read-only index {db_path} failed: {exc}") from exc
        return _OpenReadOnly(root=Path(spec.root), sql=sql, status=spec.status, connection=connection)

    def _require_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise CacheError("index is closed")
        return self._db

    def _object_path(self, relative: str) -> Path:
        path = (self.objects_dir / relative).resolve()
        root = self.objects_dir.resolve()
        if not path.is_relative_to(root):
            raise CacheError("index entry escapes the object directory")
        return path


def normalize_url(url: str) -> str:
    """Return an http(s) URL with the fragment removed."""
    candidate = url.strip()
    if not candidate or len(candidate) > 4096 or any(char in candidate for char in "\r\n\t"):
        raise InvalidUrlError("image URL is empty or not a single-line http(s) URL")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise InvalidUrlError(f"image URL must be http or https: {candidate}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def resolve_content_type(header_type: str, body: bytes) -> str:
    """Prefer sniffed image types, and accept SVG when the header says so."""
    sniffed = sniff_image_type(body)
    if sniffed is not None:
        return sniffed
    if header_type == "image/svg+xml" and _looks_like_svg(body):
        return "image/svg+xml"
    raise NotAnImageError("response is not a png, jpeg, gif, webp, or svg image")


def sniff_image_type(body: bytes) -> str | None:
    """Return a content type when `body` matches a common image signature."""
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _scheme_twin(url: str) -> str:
    parts = urlsplit(url)
    scheme = "http" if parts.scheme == "https" else "https"
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def _stored_content_type(body: bytes) -> str | None:
    sniffed = sniff_image_type(body)
    if sniffed is not None:
        return sniffed
    if _looks_like_svg(body):
        return "image/svg+xml"
    return None


def _readable_in_root(root: Path, stored: object) -> Path | None:
    if not isinstance(stored, str) or stored == "":
        return None
    root_resolved = root.resolve()
    try:
        candidate = Path(stored)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
        return None
    return resolved


def _read_only_sql(spec: ReadOnlyIndex) -> str:
    sql = (
        f"SELECT {_quote_ident(spec.path_column, 'pathColumn')} "
        f"FROM {_quote_ident(spec.table, 'table')} "
        f"WHERE {_quote_ident(spec.url_column, 'urlColumn')} = ?"
    )
    if spec.status_column is not None:
        sql += f" AND {_quote_ident(spec.status_column, 'statusColumn')} = ?"
    return sql


def _quote_ident(name: str, label: str) -> str:
    if not isinstance(name, str) or _IDENT.fullmatch(name) is None:
        raise CacheError(f"{label} is not a plain identifier: {name!r}")
    # Brackets, not double quotes: SQLite treats an unknown "name" as a string literal.
    return f"[{name}]"


def _looks_like_svg(body: bytes) -> bool:
    head = body.lstrip()[:512].lower()
    return head.startswith(b"<") and b"<svg" in head


def _user_agent() -> str:
    from origin_image_cache import __version__

    return f"origin-image-cache/{__version__}"
