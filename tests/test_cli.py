from typer.testing import CliRunner

from signalbench.cli import app

runner = CliRunner()


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("universe", "seed", "ingest"):
        assert command in result.stdout
    assert "seed-watchlist" not in result.stdout


def test_ingest_help_lists_every_source() -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    for command in ("prices", "filings", "earnings", "news", "all", "stats"):
        assert command in result.stdout


def test_universe_help_lists_refresh() -> None:
    result = runner.invoke(app, ["universe", "--help"])
    assert result.exit_code == 0
    assert "refresh" in result.stdout
