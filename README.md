# origin-image-cache

A read-through cache for images, keyed by origin URL. A hit is served from a local index directory. A miss is fetched from the origin, checked to be PNG, JPEG, GIF, WebP, or SVG, and stored. Read-only sqlite indexes can answer before that fetch. Python 3.10+, no runtime dependencies.

## Install

```bash
pip install "origin-image-cache @ git+https://github.com/kolpacksoftware/origin-image-cache.git@main"
```

Pin with a tag or commit SHA in place of `main`.

## Server

```bash
origin-image-cache serve --index ./index        # 127.0.0.1:8765 by default
```

```bash
curl -fsS -G --data-urlencode "url=https://example.com/a.png" \
  http://127.0.0.1:8765/v1/image -D - -o a.png
```

- `GET /v1/image?url=<origin-url>` returns the bytes. `X-Cache` is `HIT` from this index, `INDEX` from a read-only index, or `MISS` when the origin was fetched.
- `GET /health` returns `{"status":"ok"}`.
- Errors are JSON `{"error": ...}`: 400 missing or non-http(s) `url`, 404 origin not found, 413 too large, 415 not an image, 500 index error, 502 origin failure.

The server fetches whatever http(s) URL a client sends. That is fine on localhost. If you bind another address with `--host`, anyone who can reach the port can make this process fetch URLs.

## Options

`serve` and `get` both accept:

- `--index DIR` (required): the writable index.
- `--max-bytes N`: size limit in bytes. Default 26214400 (25 MB).
- `--read-only indexes.json`: sqlite indexes to check before fetching.
- `--fetch fetch.json`: origin User-Agent rules.

A bad config file prints `error: ...` and exits 1 at startup.

### Read-only indexes

One object or an array. Relative `db` and `root` paths resolve from the JSON file's directory.

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

`statusColumn` and `status` are optional, but set both or neither. Table and column names must be plain identifiers (letters, digits, underscore; not starting with a digit). Each index is queried for the URL and its `http`/`https` twin. The file must be inside `root`, and its type is sniffed from the bytes. Databases are opened read-only, and hits are not copied into this cache.

### Fetch User-Agent

```json
{
  "userAgent": "curl/8.0",
  "hosts": {
    "example.test": "ExampleBrowser/1.0"
  }
}
```

Both keys are required, and `hosts` may be `{}`. A host listed in `hosts` gets that agent. Every other origin gets `userAgent`. Host matching is exact and case-insensitive: `example.test` does not cover `www.example.test`, and keys cannot include a port. The agent is picked from the requested URL and kept across redirects. Without `--fetch`, the agent is `origin-image-cache/<version>`. The package ships no host list.

## One-off fetch

```bash
origin-image-cache get "https://example.com/a.png" --index ./index -o a.png   # stdout without -o
```

Prints `HIT`, `INDEX`, or `MISS` with the content type and size to stderr, and exits 1 on error.

## Library

```python
from origin_image_cache import ImageCache

with ImageCache("./index", timeout=20.0, max_bytes=25 * 1024 * 1024) as cache:
    image = cache.get("https://example.com/a.png")
image.body          # bytes
image.content_type  # e.g. "image/png"
image.hit           # False when fetched, True when already local
image.where         # "miss", "index", or "read-only"
```

Other keyword arguments: `read_only=[ReadOnlyIndex(...)]`, `user_agent="..."`, and `host_agents={"host": "agent"}`. Failures raise subclasses of `origin_image_cache.cache.CacheError`.

## Notes

- Origin fetches ignore `HTTP(S)_PROXY` environment variables.
- Nothing expires. The index is `index.sqlite` plus `objects/`. Delete the directory to clear the cache.

## Development

```bash
pip install -e ".[dev]" && pytest
```
