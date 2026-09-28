"""Look up image bytes by origin URL, fetching and storing a miss."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


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


class ImageCache:
    """Local index of origin image URLs.

    A hit reads bytes already stored for that URL. A miss fetches the URL,
    checks that the body is an image, and stores it in the index.
    """

    def __init__(
        self,
        index_dir: str | Path,
        *,
        timeout: float = 20.0,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.index_dir = Path(index_dir)
        self.objects_dir = self.index_dir / "objects"
        self.manifest_path = self.index_dir / "manifest.json"
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)

    def get(self, url: str) -> CachedImage:
        """Return image bytes for `url`, fetching and storing them on a miss."""
        normalized = normalize_url(url)
        with self._lock:
            cached = self._lookup(normalized)
            if cached is not None:
                return cached
            body, content_type = self._fetch(normalized)
            self._store(normalized, body, content_type)
            return CachedImage(url=normalized, body=body, content_type=content_type, hit=False)

    def _lookup(self, url: str) -> CachedImage | None:
        manifest = self._load_manifest()
        entry = manifest["entries"].get(url)
        if entry is None:
            return None
        path = self._object_path(entry["file"])
        if not path.is_file():
            return None
        return CachedImage(
            url=url,
            body=path.read_bytes(),
            content_type=entry["content_type"],
            hit=True,
        )

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

        manifest = self._load_manifest()
        manifest["entries"][url] = {
            "file": relative,
            "content_type": content_type,
            "size": len(body),
        }
        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        temporary_manifest = self.manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_text(payload, encoding="utf-8")
        os.replace(temporary_manifest, self.manifest_path)

    def _load_manifest(self) -> dict:
        if not self.manifest_path.is_file():
            return {"version": 1, "entries": {}}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CacheError(f"index manifest is not valid JSON: {self.manifest_path}") from exc
        if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
            raise CacheError(f"unsupported index manifest: {self.manifest_path}")
        return data

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


def _looks_like_svg(body: bytes) -> bool:
    head = body.lstrip()[:512].lower()
    return head.startswith(b"<") and b"<svg" in head


def _user_agent() -> str:
    from origin_image_cache import __version__

    return f"origin-image-cache/{__version__}"
