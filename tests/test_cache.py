from pathlib import Path

import pytest

from origin_image_cache import ImageCache
from origin_image_cache.cache import InvalidUrlError, NotAnImageError, OriginNotFoundError
from tests.support import make_png, serve_origin


def test_miss_then_hit_fetches_origin_once(tmp_path: Path) -> None:
    png = make_png()
    cache = ImageCache(tmp_path / "index")
    with serve_origin(png, "image/png") as (url, state):
        miss = cache.get(url)
        hit = cache.get(url)

    assert state["hits"] == 1
    assert miss.hit is False
    assert hit.hit is True
    assert miss.body == png
    assert hit.body == png
    assert hit.content_type == "image/png"
    assert (tmp_path / "index" / "manifest.json").is_file()


def test_octet_stream_png_is_sniffed(tmp_path: Path) -> None:
    png = make_png()
    cache = ImageCache(tmp_path / "index")
    with serve_origin(png, "application/octet-stream") as (url, state):
        image = cache.get(url)

    assert state["hits"] == 1
    assert image.content_type == "image/png"
    assert image.body == png


def test_html_response_is_not_stored(tmp_path: Path) -> None:
    cache = ImageCache(tmp_path / "index")
    with serve_origin(b"<html>nope</html>", "text/html") as (url, state):
        with pytest.raises(NotAnImageError):
            cache.get(url)
        with pytest.raises(NotAnImageError):
            cache.get(url)

    assert state["hits"] == 2
    assert not (tmp_path / "index" / "manifest.json").exists()


def test_origin_404(tmp_path: Path) -> None:
    cache = ImageCache(tmp_path / "index")
    with serve_origin(b"missing", "text/plain", status=404) as (url, _state):
        with pytest.raises(OriginNotFoundError):
            cache.get(url)


def test_rejects_non_http_url(tmp_path: Path) -> None:
    cache = ImageCache(tmp_path / "index")
    with pytest.raises(InvalidUrlError):
        cache.get("file:///tmp/pic.png")
