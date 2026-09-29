import hashlib
import sqlite3
from pathlib import Path

import pytest

from origin_image_cache import ImageCache
from origin_image_cache import __version__
from origin_image_cache.cache import (
    CacheError,
    InvalidUrlError,
    NotAnImageError,
    OriginNotFoundError,
    ReadOnlyIndex,
    TooLargeError,
    load_fetch_config,
)
from tests.support import make_png, serve_origin


def test_miss_then_hit_fetches_origin_once(tmp_path: Path) -> None:
    png = make_png()
    index = tmp_path / "index"
    with ImageCache(index) as cache, serve_origin(png, "image/png") as (url, state):
        miss = cache.get(url)
        hit = cache.get(url)

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    assert state["hits"] == 1
    assert miss.hit is False
    assert hit.hit is True
    assert miss.where == "miss"
    assert hit.where == "index"
    assert miss.body == png
    assert hit.body == png
    assert hit.content_type == "image/png"
    assert (index / "index.sqlite").is_file()
    assert not (index / "manifest.json").exists()
    assert (index / "objects" / digest[:2] / digest).is_file()
    assert _object_count(index) == 1


def test_read_only_hit_does_not_fetch(tmp_path: Path) -> None:
    png = make_png(rgb=(0, 255, 0))
    root = tmp_path / "read only" / "files"
    root.mkdir(parents=True)
    (root / "pic.png").write_bytes(png)
    db = tmp_path / "read only" / "images.sqlite"
    with serve_origin(make_png(), "image/png") as (url, state):
        _sqlite(db, [(url, "pic.png")])
        spec = _spec(db, root)
        with ImageCache(tmp_path / "index", read_only=[spec]) as cache:
            first = cache.get(url)
            second = cache.get(url)

    assert state["hits"] == 0
    assert first.hit is True
    assert second.hit is True
    assert first.where == "read-only"
    assert second.where == "read-only"
    assert first.body == png
    assert first.content_type == "image/png"
    assert _object_count(tmp_path / "index") == 0


def test_read_only_scheme_twin(tmp_path: Path) -> None:
    png = make_png(rgb=(0, 0, 255))
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(png)
    db = tmp_path / "images.sqlite"
    with serve_origin(make_png(), "image/png") as (url, state):
        twin = "https://" + url.removeprefix("http://")
        _sqlite(db, [(twin, "pic.png")])
        with ImageCache(tmp_path / "index", read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 0
    assert image.where == "read-only"
    assert image.url == url
    assert image.body == png


def test_read_only_missing_file_fetches(tmp_path: Path) -> None:
    png = make_png()
    root = tmp_path / "files"
    root.mkdir()
    db = tmp_path / "images.sqlite"
    with serve_origin(png, "image/png") as (url, state):
        _sqlite(db, [(url, "missing.png")])
        with ImageCache(tmp_path / "index", read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "miss"
    assert image.body == png


def test_read_only_wrong_status_fetches(tmp_path: Path) -> None:
    origin_png = make_png(rgb=(255, 0, 0))
    other_png = make_png(rgb=(0, 255, 0))
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(other_png)
    db = tmp_path / "images.sqlite"
    with serve_origin(origin_png, "image/png") as (url, state):
        _sqlite(db, [(url, "pic.png", "pending")], with_status=True)
        spec = _spec(db, root, status_column="status", status="ok")
        with ImageCache(tmp_path / "index", read_only=[spec]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "miss"
    assert image.body == origin_png


def test_read_only_status_match(tmp_path: Path) -> None:
    png = make_png(rgb=(0, 255, 0))
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(png)
    db = tmp_path / "images.sqlite"
    with serve_origin(make_png(), "image/png") as (url, state):
        _sqlite(db, [(url, "pic.png", "ok")], with_status=True)
        spec = _spec(db, root, status_column="status", status="ok")
        with ImageCache(tmp_path / "index", read_only=[spec]) as cache:
            image = cache.get(url)

    assert state["hits"] == 0
    assert image.where == "read-only"
    assert image.body == png


def test_read_only_parent_path_fetches(tmp_path: Path) -> None:
    origin_png = make_png(rgb=(255, 0, 0))
    secret = make_png(rgb=(0, 0, 255))
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.png").write_bytes(secret)
    db = tmp_path / "images.sqlite"
    with serve_origin(origin_png, "image/png") as (url, state):
        _sqlite(db, [(url, "../secret.png")])
        with ImageCache(tmp_path / "index", read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "miss"
    assert image.body == origin_png


def test_read_only_symlink_fetches(tmp_path: Path) -> None:
    origin_png = make_png(rgb=(255, 0, 0))
    secret = make_png(rgb=(0, 0, 255))
    outside = tmp_path / "outside.png"
    outside.write_bytes(secret)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.png").symlink_to(outside)
    db = tmp_path / "images.sqlite"
    with serve_origin(origin_png, "image/png") as (url, state):
        _sqlite(db, [(url, "link.png")])
        with ImageCache(tmp_path / "index", read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "miss"
    assert image.body == origin_png


def test_read_only_non_image_fetches(tmp_path: Path) -> None:
    png = make_png()
    root = tmp_path / "files"
    root.mkdir()
    (root / "note.html").write_bytes(b"<html>nope</html>")
    db = tmp_path / "images.sqlite"
    with serve_origin(png, "image/png") as (url, state):
        _sqlite(db, [(url, "note.html")])
        with ImageCache(tmp_path / "index", read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "miss"
    assert image.body == png


def test_read_only_too_large(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (root / "big.bin").write_bytes(b"x" * 50)
    db = tmp_path / "images.sqlite"
    url = "https://example.com/big.png"
    _sqlite(db, [(url, "big.bin")])
    with ImageCache(tmp_path / "index", max_bytes=10, read_only=[_spec(db, root)]) as cache:
        with pytest.raises(TooLargeError):
            cache.get(url)


def test_read_only_rejects_bad_identifier(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "missing.sqlite", tmp_path, table="images;drop")
    with pytest.raises(CacheError, match="plain identifier"):
        ImageCache(tmp_path / "index", read_only=[spec])


def test_read_only_missing_database(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "missing.sqlite", tmp_path)
    with pytest.raises(CacheError, match="not readable"):
        ImageCache(tmp_path / "index", read_only=[spec])


def test_read_only_missing_column(tmp_path: Path) -> None:
    db = tmp_path / "images.sqlite"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE images (url TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(CacheError, match="no such column"):
        ImageCache(tmp_path / "index", read_only=[_spec(db, tmp_path)])


def test_missing_object_file_is_refetched(tmp_path: Path) -> None:
    png = make_png()
    index = tmp_path / "index"
    with ImageCache(index) as cache, serve_origin(png, "image/png") as (url, state):
        cache.get(url)
        for path in (index / "objects").rglob("*"):
            if path.is_file():
                path.unlink()
        again = cache.get(url)
        stored = cache.get(url)

    assert state["hits"] == 2
    assert again.where == "miss"
    assert again.body == png
    assert stored.where == "index"
    assert _object_count(index) == 1


def test_own_index_wins_over_read_only(tmp_path: Path) -> None:
    ours = make_png(rgb=(255, 0, 0))
    theirs = make_png(rgb=(0, 255, 0))
    index = tmp_path / "index"
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(theirs)
    db = tmp_path / "images.sqlite"
    with serve_origin(ours, "image/png") as (url, state):
        with ImageCache(index) as cache:
            cache.get(url)
        _sqlite(db, [(url, "pic.png")])
        with ImageCache(index, read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "index"
    assert image.body == ours


def test_missing_own_file_uses_read_only(tmp_path: Path) -> None:
    png = make_png(rgb=(0, 255, 0))
    index = tmp_path / "index"
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(png)
    db = tmp_path / "images.sqlite"
    with serve_origin(make_png(), "image/png") as (url, state):
        with ImageCache(index) as cache:
            cache.get(url)
            for path in (index / "objects").rglob("*"):
                if path.is_file():
                    path.unlink()
        _sqlite(db, [(url, "pic.png")])
        with ImageCache(index, read_only=[_spec(db, root)]) as cache:
            image = cache.get(url)

    assert state["hits"] == 1
    assert image.where == "read-only"
    assert image.body == png


def test_octet_stream_png_is_sniffed(tmp_path: Path) -> None:
    png = make_png()
    with ImageCache(tmp_path / "index") as cache, serve_origin(png, "application/octet-stream") as (url, state):
        image = cache.get(url)

    assert state["hits"] == 1
    assert image.content_type == "image/png"
    assert image.body == png


def test_html_response_is_not_stored(tmp_path: Path) -> None:
    index = tmp_path / "index"
    with ImageCache(index) as cache, serve_origin(b"<html>nope</html>", "text/html") as (url, state):
        with pytest.raises(NotAnImageError):
            cache.get(url)
        with pytest.raises(NotAnImageError):
            cache.get(url)

    assert state["hits"] == 2
    assert (index / "index.sqlite").is_file()
    assert not (index / "manifest.json").exists()
    assert _object_count(index) == 0


def test_origin_404(tmp_path: Path) -> None:
    with ImageCache(tmp_path / "index") as cache, serve_origin(b"missing", "text/plain", status=404) as (url, _state):
        with pytest.raises(OriginNotFoundError):
            cache.get(url)


def test_rejects_non_http_url(tmp_path: Path) -> None:
    with ImageCache(tmp_path / "index") as cache:
        with pytest.raises(InvalidUrlError):
            cache.get("file:///tmp/pic.png")


def test_default_user_agent_is_package_version(tmp_path: Path) -> None:
    png = make_png()
    with ImageCache(tmp_path / "index") as cache, serve_origin(png, "image/png") as (url, state):
        cache.get(url)

    assert state["user_agents"] == [f"origin-image-cache/{__version__}"]


def test_configured_user_agent_used_for_unlisted_host(tmp_path: Path) -> None:
    png = make_png()
    with ImageCache(tmp_path / "index", user_agent="curl/8.0", host_agents={"example.test": "ExampleBrowser/1.0"}) as cache:
        with serve_origin(png, "image/png") as (url, state):
            cache.get(url)

    assert state["user_agents"] == ["curl/8.0"]


def test_host_user_agent_override(tmp_path: Path) -> None:
    png = make_png()
    with ImageCache(tmp_path / "index", user_agent="curl/8.0", host_agents={"127.0.0.1": "ExampleBrowser/1.0"}) as cache:
        with serve_origin(png, "image/png") as (url, state):
            cache.get(url)

    assert state["user_agents"] == ["ExampleBrowser/1.0"]


def test_load_fetch_config(tmp_path: Path) -> None:
    path = tmp_path / "fetch.json"
    path.write_text(
        '{"userAgent": "curl/8.0", "hosts": {"Example.TEST": "ExampleBrowser/1.0"}}',
        encoding="utf-8",
    )
    user_agent, hosts = load_fetch_config(path)
    assert user_agent == "curl/8.0"
    assert hosts == {"example.test": "ExampleBrowser/1.0"}


def test_load_fetch_config_rejects_bad_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(CacheError, match="cannot read"):
        load_fetch_config(missing)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(CacheError, match="not valid JSON"):
        load_fetch_config(invalid)

    unknown = tmp_path / "unknown.json"
    unknown.write_text('{"userAgent": "curl/8.0", "hosts": {}, "extra": 1}', encoding="utf-8")
    with pytest.raises(CacheError, match="unknown keys"):
        load_fetch_config(unknown)

    empty_agent = tmp_path / "empty.json"
    empty_agent.write_text('{"userAgent": "", "hosts": {}}', encoding="utf-8")
    with pytest.raises(CacheError, match="userAgent"):
        load_fetch_config(empty_agent)

    newline = tmp_path / "newline.json"
    newline.write_text('{"userAgent": "curl/8.0\\n", "hosts": {}}', encoding="utf-8")
    with pytest.raises(CacheError, match="CR or LF"):
        load_fetch_config(newline)

    bad_host = tmp_path / "host.json"
    bad_host.write_text('{"userAgent": "curl/8.0", "hosts": {"example.test:443": "x"}}', encoding="utf-8")
    with pytest.raises(CacheError, match="not a hostname"):
        load_fetch_config(bad_host)

    twice = tmp_path / "twice.json"
    twice.write_text('{"userAgent": "curl/8.0", "hosts": {"a.test": "x", "A.test": "y"}}', encoding="utf-8")
    with pytest.raises(CacheError, match="listed twice"):
        load_fetch_config(twice)


def _spec(db: Path, root: Path, **kwargs: str) -> ReadOnlyIndex:
    return ReadOnlyIndex(
        db=db,
        table=kwargs.get("table", "images"),
        root=root,
        url_column="url",
        path_column="path",
        status_column=kwargs.get("status_column"),
        status=kwargs.get("status"),
    )


def _sqlite(path: Path, rows: list[tuple[str, ...]], *, with_status: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        if with_status:
            connection.execute("CREATE TABLE images (url TEXT, path TEXT, status TEXT)")
            connection.executemany("INSERT INTO images (url, path, status) VALUES (?, ?, ?)", rows)
        else:
            connection.execute("CREATE TABLE images (url TEXT, path TEXT)")
            connection.executemany("INSERT INTO images (url, path) VALUES (?, ?)", rows)
        connection.commit()
    finally:
        connection.close()


def _object_count(index: Path) -> int:
    connection = sqlite3.connect(index / "index.sqlite")
    try:
        row = connection.execute("SELECT COUNT(*) FROM objects").fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])
