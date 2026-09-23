from typer.testing import CliRunner

from signalbench.cli import app

runner = CliRunner()


def test_help_lists_seed_and_ingest() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "seed" in result.stdout
    assert "seed-watchlist" not in result.stdout
    assert "ingest" in result.stdout
