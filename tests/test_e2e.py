import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from origin_image_cache import ImageCache
from origin_image_cache.cache import ReadOnlyIndex
from origin_image_cache.cli import main
from origin_image_cache.server import make_server
from tests.support import make_png, serve_origin


def _http_get(url: str) -> tuple[int, dict[str, str], bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.status, headers, response.read()
    except urllib.error.HTTPError as exc:
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return exc.code, headers, exc.read()


def test_http_miss_then_hit(tmp_path: Path) -> None:
    png = make_png(width=8, height=8)
    cache = ImageCache(tmp_path / "index")
    server = make_server(cache, "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with serve_origin(png, "image/png") as (origin_url, state):
            health_status, _, health_body = _http_get(f"http://127.0.0.1:{port}/health")
            request_url = "http://127.0.0.1:%s/v1/image?%s" % (
                port,
                urllib.parse.urlencode({"url": origin_url}),
            )
            miss_status, miss_headers, miss_body = _http_get(request_url)
            hit_status, hit_headers, hit_body = _http_get(request_url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        cache.close()

    assert health_status == 200
    assert json.loads(health_body) == {"status": "ok"}
    assert state["hits"] == 1
    assert miss_status == 200
    assert hit_status == 200
    assert miss_headers["x-cache"] == "MISS"
    assert hit_headers["x-cache"] == "HIT"
    assert miss_headers["content-type"] == "image/png"
    assert miss_body == png
    assert hit_body == png


def test_cli_get_writes_file_then_hits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    png = make_png()
    index = tmp_path / "index"
    output = tmp_path / "out.png"
    with serve_origin(png, "image/png") as (origin_url, state):
        assert main(["get", origin_url, "--index", str(index), "-o", str(output)]) == 0
        assert main(["get", origin_url, "--index", str(index), "-o", str(output)]) == 0

    captured = capsys.readouterr()
    assert "MISS image/png" in captured.err
    assert "HIT image/png" in captured.err
    assert output.read_bytes() == png
    assert state["hits"] == 1


def test_http_rejects_non_image(tmp_path: Path) -> None:
    cache = ImageCache(tmp_path / "index")
    server = make_server(cache, "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with serve_origin(b"<html>nope</html>", "text/html") as (origin_url, _state):
            request_url = "http://127.0.0.1:%s/v1/image?%s" % (
                port,
                urllib.parse.urlencode({"url": origin_url}),
            )
            status, headers, body = _http_get(request_url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        cache.close()

    assert status == 415
    assert headers["content-type"].startswith("application/json")
    assert "not a png" in json.loads(body)["error"]


def test_http_read_only_index_header(tmp_path: Path) -> None:
    cached = make_png(rgb=(0, 255, 0))
    origin = make_png(rgb=(255, 0, 0))
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(cached)
    db = tmp_path / "images.sqlite"
    with serve_origin(origin, "image/png") as (origin_url, state):
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE images (url TEXT, path TEXT)")
        connection.execute("INSERT INTO images (url, path) VALUES (?, ?)", (origin_url, "pic.png"))
        connection.commit()
        connection.close()
        cache = ImageCache(
            tmp_path / "index",
            read_only=[
                ReadOnlyIndex(db=db, table="images", root=root, url_column="url", path_column="path")
            ],
        )
        server = make_server(cache, "127.0.0.1", 0, quiet=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request_url = "http://127.0.0.1:%s/v1/image?%s" % (
                port,
                urllib.parse.urlencode({"url": origin_url}),
            )
            status, headers, body = _http_get(request_url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            cache.close()

    assert state["hits"] == 0
    assert status == 200
    assert headers["x-cache"] == "INDEX"
    assert headers["content-type"] == "image/png"
    assert body == cached


def test_cli_read_only_prints_index(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    png = make_png()
    root = tmp_path / "files"
    root.mkdir()
    (root / "pic.png").write_bytes(png)
    db = tmp_path / "images.sqlite"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE images (url TEXT, path TEXT)")
    connection.execute(
        "INSERT INTO images (url, path) VALUES (?, ?)",
        ("https://example.com/a.png", "pic.png"),
    )
    connection.commit()
    connection.close()
    config = tmp_path / "indexes.json"
    config.write_text(
        json.dumps(
            {
                "db": db.name,
                "table": "images",
                "root": root.name,
                "urlColumn": "url",
                "pathColumn": "path",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.png"
    code = main(
        [
            "get",
            "https://example.com/a.png",
            "--index",
            str(tmp_path / "index"),
            "--read-only",
            str(config),
            "-o",
            str(output),
        ]
    )

    assert code == 0
    assert "INDEX image/png" in capsys.readouterr().err
    assert output.read_bytes() == png


def test_cli_max_bytes_rejects_large_origin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    png = make_png()
    assert len(png) > 10
    output = tmp_path / "out.png"
    with serve_origin(png, "image/png") as (origin_url, state):
        code = main(
            [
                "get",
                origin_url,
                "--index",
                str(tmp_path / "index"),
                "--max-bytes",
                "10",
                "-o",
                str(output),
            ]
        )

    assert code == 1
    assert "exceeds" in capsys.readouterr().err
    assert state["hits"] == 1
    assert not output.exists()


def test_cli_fetch_sends_configured_agent(tmp_path: Path) -> None:
    png = make_png()
    config = tmp_path / "fetch.json"
    config.write_text(
        '{"userAgent": "curl/8.0", "hosts": {"example.test": "ExampleBrowser/1.0"}}',
        encoding="utf-8",
    )
    with serve_origin(png, "image/png") as (origin_url, state):
        code = main(
            [
                "get",
                origin_url,
                "--index",
                str(tmp_path / "index"),
                "--fetch",
                str(config),
                "-o",
                str(tmp_path / "out.png"),
            ]
        )

    assert code == 0
    assert state["user_agents"] == ["curl/8.0"]


def test_cli_bad_fetch_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "fetch.json"
    config.write_text("{", encoding="utf-8")
    code = main(
        [
            "get",
            "https://example.com/a.png",
            "--index",
            str(tmp_path / "index"),
            "--fetch",
            str(config),
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "error:" in captured.err
    assert "not valid JSON" in captured.err


def test_cli_bad_read_only_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "indexes.json"
    config.write_text("{", encoding="utf-8")
    code = main(
        [
            "get",
            "https://example.com/a.png",
            "--index",
            str(tmp_path / "index"),
            "--read-only",
            str(config),
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "error:" in captured.err
    assert "not valid JSON" in captured.err
