# origin-image-cache

Serve image bytes for an origin URL. A client asks with the original image URL and receives the bytes from a configured local index, or the service fetches the URL and stores the miss.

Install from PyPI, a local clone, or a Git URL (see below).

## Install

**From PyPI** (when published):

```bash
pip install origin-image-cache
```

**From a local clone** (development):

```bash
pip install -e "/path/to/origin-image-cache[dev]"
```

**From a Git URL**:

```bash
pip install "origin-image-cache @ git+https://github.com/kolpacksoftware/origin-image-cache.git@main"
# or
pip install "origin-image-cache @ git+ssh://git@github.com/kolpacksoftware/origin-image-cache.git@main"
```

You can pin a branch (`@main`), tag (`@v0.1.0`), or commit (`@abc1234`).

## CLI

```bash
origin-image-cache --version

origin-image-cache serve --index ./index --host 127.0.0.1 --port 8765

curl -fsS "http://127.0.0.1:8765/v1/image?url=https%3A%2F%2Fexample.com%2Fa.png" -D - -o a.png

origin-image-cache get "https://example.com/a.png" --index ./index -o a.png
```

`serve` listens on `127.0.0.1:8765` by default. `GET /v1/image?url=<origin-url>` returns the image bytes. The response header `X-Cache` is `MISS` the first time and `HIT` after the bytes are stored. `GET /health` returns `{"status":"ok"}`.

`get` writes the same bytes to `-o` or to stdout. A miss fetches the origin URL directly (environment proxies are ignored) and stores the body in the index. The response must be a PNG, JPEG, GIF, WebP, or SVG.

## Library

```python
from origin_image_cache import ImageCache

image = ImageCache("./index").get("https://example.com/a.png")
image.body          # bytes
image.content_type  # for example "image/png"
image.hit           # False on the fetch, True after it is stored
```
