# origin-image-cache

A read-through cache for images, keyed by origin URL. A hit is served from a local index directory; a miss is fetched from the origin, checked to be PNG, JPEG, GIF, WebP, or SVG, and stored. Read-only sqlite indexes can answer before that fetch. Python 3.10+, no runtime dependencies.

## Install

```bash
pip install "origin-image-cache @ git+https://github.com/kolpacksoftware/origin-image-cache.git@main"
```

Pin with `@v0.2.0` or a commit SHA. `pip install origin-image-cache` will work once it is on PyPI.

## Server

```bash
origin-image-cache serve --index ./index        # 127.0.0.1:8765 by default
```

```bash
curl -fsS -G --data-urlencode "url=https://example.com/a.png" \
  http://127.0.0.1:8765/v1/image -D - -o a.png
```

- `GET /v1/image?url=<origin-url>` returns the bytes. `X-Cache` is `MISS` when the origin is fetched, `HIT` when the bytes come from this index, and `INDEX` when they come from a read-only index.
- `GET /health` returns `{"status":"ok"}`.
- Errors are JSON `{"error": ...}`: 400 missing or non-http(s) `url`, 404 origin not found, 413 too large, 415 not an image, 502 origin failure.
- `--max-bytes` raises the size limit (default 25 MB, 26214400 bytes).
- `--read-only indexes.json` adds sqlite indexes that are opened read-only and never written.

The server fetches whatever http(s) URL a client sends. That is fine on localhost; if you bind another address with `--host`, anyone who can reach the port can make this process fetch URLs.

### Read-only indexes

`indexes.json` is one object, or an array of objects. Relative paths are resolved from the JSON file's directory.

```json
[
  {
    "db": "../catalog/images.sqlite",
    "table": "images",
    "root": "../catalog/files",
    "urlColumn": "url",
    "pathColumn": "path",
    "statusColumn": "status",
    "status": "ok"
  }
]
```

`statusColumn` and `status` are optional and must be set together. Table and column names must be plain identifiers: a letter or underscore, then letters, digits, or underscores. Before a fetch, each index is queried for the URL and its `http`/`https` twin. The file must sit inside `root`. The type is sniffed from the bytes. A hit is not copied into this cache.

## One-off fetch

```bash
origin-image-cache get "https://example.com/a.png" --index ./index -o a.png   # stdout without -o
```

Prints `HIT`, `INDEX`, or `MISS` with the content type and size to stderr, and exits 1 on error. `--read-only` and `--max-bytes` work the same way as `serve`.

## Library

```python
from origin_image_cache import ImageCache

image = ImageCache("./index", timeout=20.0, max_bytes=25 * 1024 * 1024).get("https://example.com/a.png")
image.body          # bytes
image.content_type  # e.g. "image/png"
image.hit           # False on the first fetch, True when bytes were already local
image.where         # "miss", "index", or "read-only"
```

Pass `read_only=[ReadOnlyIndex(...)]` to consult other sqlite files before fetching. Failures raise subclasses of `origin_image_cache.cache.CacheError`.

## Notes

- Origin fetches ignore `HTTP(S)_PROXY` environment variables.
- Nothing expires. The index is `index.sqlite` plus `objects/`. Delete the directory to clear the cache.
- A 0.1 directory that only has `manifest.json` is ignored. Delete it and let 0.2.0 create `index.sqlite`.

## Development

```bash
pip install -e ".[dev]" && pytest
```
