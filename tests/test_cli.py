from origin_image_cache import __version__
from origin_image_cache.cli import main


def test_version_flag(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
