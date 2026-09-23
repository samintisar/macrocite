from pathlib import Path

from signalbench.ingest.cdr import YF_SECTOR_TO_GICS, load_universe

UNIVERSE = Path(__file__).resolve().parents[1] / "data" / "cdr_universe.yaml"


def test_checked_in_universe_is_well_formed() -> None:
    entries = load_universe(UNIVERSE)
    assert len(entries) >= 30
    assert all(entry.price_symbol == f"{entry.cdr_symbol}.NE" for entry in entries)
    assert {entry.sector for entry in entries} <= set(YF_SECTOR_TO_GICS.values())
    assert len({entry.cdr_symbol for entry in entries}) == len(entries)
