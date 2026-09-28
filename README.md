# origin-image-cache

A read-through cache for images, keyed by origin URL. A hit is served from a local index directory; a miss is fetched from the origin, checked to be PNG, JPEG, GIF, WebP, or SVG, and stored. Python 3.10+, no runtime dependencies.

## Install

```bash
pip install "origin-image-cache @ git+https://github.com/kolpacksoftware/origin-image-cache.git@main"
```

Pin with `@v0.1.0` or a commit SHA. `pip install origin-image-cache` will work once it is on PyPI.

## Server

```bash
origin-image-cache serve --index ./index        # 127.0.0.1:8765 by default
```

```bash
curl -fsS -G --data-urlencode "url=https://example.com/a.png" \
  http://127.0.0.1:8765/v1/image -D - -o a.png
```

- `GET /v1/image?url=<origin-url>` returns the bytes. `X-Cache: MISS` on the first fetch, `HIT` after.
- `GET /health` returns `{"status":"ok"}`.
- Errors are JSON `{"error": ...}`: 400 missing or non-http(s) `url`, 404 origin not found, 413 too large, 415 not an image, 502 origin failure.

The server fetches whatever http(s) URL a client sends. That is fine on localhost; if you bind another address with `--host`, anyone who can reach the port can make this process fetch URLs.

## One-off fetch

```bash
origin-image-cache get "https://example.com/a.png" --index ./index -o a.png   # stdout without -o
```

Prints `HIT` or `MISS` with the content type and size to stderr, and exits 1 on error.

## Library

```python
from origin_image_cache import ImageCache

image = ImageCache("./index", timeout=20.0, max_bytes=25 * 1024 * 1024).get("https://example.com/a.png")
image.body          # bytes
image.content_type  # e.g. "image/png"
image.hit           # False on the first fetch, True afterwards
```

Failures raise subclasses of `origin_image_cache.cache.CacheError`.

## Notes

- Origin fetches ignore `HTTP(S)_PROXY` environment variables.
- Nothing expires. The index is `manifest.json` plus `objects/`; delete it to clear the cache.

## Development

```bash
pip install -e ".[dev]" && pytest
```
