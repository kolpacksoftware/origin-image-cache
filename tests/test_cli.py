import pytest

from origin_image_cache import __version__
from origin_image_cache.cli import main


def test_version_flag(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_rejects_non_positive_max_bytes(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["get", "https://example.com/a.png", "--index", "idx", "--max-bytes", "0"])
    assert caught.value.code != 0
    assert "max-bytes" in capsys.readouterr().err
