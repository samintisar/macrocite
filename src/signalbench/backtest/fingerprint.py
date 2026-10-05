import hashlib
import math
from collections.abc import Iterable, Sequence
from datetime import date

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.readings import DocumentReading


def signal_set_fingerprint(signal_ids: list[str]) -> str:
    joined = ",".join(sorted(signal_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def series_summary(symbol: str, bars: Sequence[AdjustedBar]) -> str:
    """symbol|first_date|last_date|row_count|round(sum(adj_close), 4)."""
    if not bars:
        return f"{symbol}|||0|0.0000"
    total = round(math.fsum(bar.close for bar in bars), 4)
    return f"{symbol}|{bars[0].date.isoformat()}|{bars[-1].date.isoformat()}|{len(bars)}|{total:.4f}"


def earnings_summary(earnings: Iterable[tuple[str, date]]) -> str:
    """earnings|count|SHA-256 of the sorted, distinct symbol|YYYY-MM-DD pairs."""
    pairs = sorted({f"{symbol}|{day.isoformat()}" for symbol, day in earnings})
    return f"earnings|{len(pairs)}|{signal_set_fingerprint(pairs)}"


def readings_summary(readings: Iterable[tuple[str, DocumentReading]]) -> str:
    """jev|count|SHA-256 of the sorted symbol|legal close|document|probabilities (6 dp) rows."""
    rows = sorted(
        f"{symbol}|{r.legal_close.isoformat()}|{r.document_id}|{r.p_negative:.6f}|"
        f"{r.p_neutral:.6f}|{r.p_positive:.6f}|{r.p_routine:.6f}"
        for symbol, r in readings
    )
    return f"jev|{len(rows)}|{signal_set_fingerprint(rows)}"


def data_fingerprint(
    series: Iterable[tuple[str, Sequence[AdjustedBar]]],
    earnings: Iterable[tuple[str, date]],
    readings: Iterable[tuple[str, DocumentReading]] | None = None,
) -> str:
    """SHA-256 over the sorted per-series summaries of every input price series, plus a
    summary of every (symbol, earnings date) the run used (spec 02), plus, for runs that read
    Jev (spec 03), a summary of every reading. `readings=None` keeps Jev-off fingerprints as
    they were."""
    summaries = [series_summary(symbol, bars) for symbol, bars in series]
    extra = [] if readings is None else [readings_summary(readings)]
    return signal_set_fingerprint([*summaries, earnings_summary(earnings), *extra])
