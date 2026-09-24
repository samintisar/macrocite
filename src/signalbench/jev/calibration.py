"""Calibration report (spec 03, information only). Pure: samples in, tables and markdown out.

Nothing here feeds back into theta or the 0.70 positive threshold. Changing either after reading
the report is an owner decision recorded in the spec 03 changelog.
"""

import math
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from signalbench.jev.questions import JEV_RELEASE

BENCHMARK_SYMBOL = "QQQ"
HORIZON_SESSIONS = 5
MOVE_THRESHOLD = 0.02
DECILES = 10
Label = Literal["down", "flat", "up"]
LABELS: tuple[Label, ...] = ("down", "flat", "up")
IMPACT_TO_LABEL: dict[str, Label] = {"negative": "down", "neutral": "flat", "positive": "up"}


@dataclass(frozen=True)
class Sample:
    legal_close: date
    p_negative: float
    p_neutral: float
    p_positive: float
    label: Label


def label_for(
    legal_close: date,
    closes: Mapping[date, float],
    benchmark: Mapping[date, float],
    sessions: Sequence[date],
) -> Label | None:
    """The symbol's adj-close return minus QQQ's from the legal close to 5 sessions later:
    above +2% is up, below -2% is down, else flat. None when a price or session is missing.
    `sessions` is sorted. The excess is rounded to 10 decimals so an exact 2% stays flat."""
    index = bisect_left(sessions, legal_close)
    if index >= len(sessions) or sessions[index] != legal_close:
        return None
    if index + HORIZON_SESSIONS >= len(sessions):
        return None
    later = sessions[index + HORIZON_SESSIONS]
    start, end = closes.get(legal_close), closes.get(later)
    b_start, b_end = benchmark.get(legal_close), benchmark.get(later)
    if start is None or end is None or b_start is None or b_end is None:
        return None
    excess = round((end / start - 1.0) - (b_end / b_start - 1.0), 10)
    if excess > MOVE_THRESHOLD:
        return "up"
    if excess < -MOVE_THRESHOLD:
        return "down"
    return "flat"


def decile(p: float) -> int:
    """Bucket 0 is [0, 0.1), ..., bucket 9 is [0.9, 1.0] (1.0 included)."""
    return min(math.floor(p * DECILES + 1e-9), DECILES - 1)


@dataclass(frozen=True)
class Bucket:
    index: int
    count: int
    mean_predicted: float | None
    observed_rate: float | None

    @property
    def label(self) -> str:
        return f"{self.index / DECILES:.1f}-{(self.index + 1) / DECILES:.1f}"


def reliability(pairs: Sequence[tuple[float, bool]]) -> list[Bucket]:
    """Ten buckets of (predicted probability, outcome happened)."""
    grouped: dict[int, list[tuple[float, bool]]] = {i: [] for i in range(DECILES)}
    for p, happened in pairs:
        grouped[decile(p)].append((p, happened))
    return [
        Bucket(
            index=i,
            count=len(rows),
            mean_predicted=math.fsum(p for p, _ in rows) / len(rows) if rows else None,
            observed_rate=sum(1 for _, hit in rows if hit) / len(rows) if rows else None,
        )
        for i, rows in grouped.items()
    ]


def ece(buckets: Sequence[Bucket]) -> float | None:
    """Expected calibration error: sum over buckets of (count / N) x |mean predicted - observed|."""
    total = sum(bucket.count for bucket in buckets)
    if total == 0:
        return None
    return math.fsum(
        bucket.count / total * abs(bucket.mean_predicted - bucket.observed_rate)
        for bucket in buckets
        if bucket.mean_predicted is not None and bucket.observed_rate is not None
    )


def _argmax_label(sample: Sample) -> Label:
    """Ties resolve in the order negative, neutral, positive."""
    options = (
        ("negative", sample.p_negative),
        ("neutral", sample.p_neutral),
        ("positive", sample.p_positive),
    )
    return IMPACT_TO_LABEL[max(options, key=lambda item: item[1])[0]]


@dataclass(frozen=True)
class Section:
    name: str
    samples: int
    positive: list[Bucket]
    positive_ece: float | None
    negative: list[Bucket]
    negative_ece: float | None
    accuracy: float | None
    majority_label: Label | None
    majority_rate: float | None
    label_counts: dict[str, int]


def calibrate(name: str, samples: Sequence[Sample]) -> Section:
    positive = reliability([(s.p_positive, s.label == "up") for s in samples])
    negative = reliability([(s.p_negative, s.label == "down") for s in samples])
    counts = Counter(s.label for s in samples)
    majority: Label | None = None
    rate: float | None = None
    accuracy: float | None = None
    if samples:
        top = max(LABELS, key=lambda name: counts[name])  # ties: down, flat, up
        majority, rate = top, counts[top] / len(samples)
        accuracy = sum(1 for s in samples if _argmax_label(s) == s.label) / len(samples)
    return Section(
        name=name,
        samples=len(samples),
        positive=positive,
        positive_ece=ece(positive),
        negative=negative,
        negative_ece=ece(negative),
        accuracy=accuracy,
        majority_label=majority,
        majority_rate=rate,
        label_counts={label: counts[label] for label in LABELS},
    )


def calibration_sections(samples: Sequence[Sample]) -> list[Section]:
    """All documents, then split at Jev's release (2026-09-15) by legal close."""
    release = JEV_RELEASE.isoformat()
    return [
        calibrate("All documents", samples),
        calibrate(
            f"Legal close before {release}", [s for s in samples if s.legal_close < JEV_RELEASE]
        ),
        calibrate(
            f"Legal close on or after {release}",
            [s for s in samples if s.legal_close >= JEV_RELEASE],
        ),
    ]


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _table(title: str, buckets: Sequence[Bucket], error: float | None) -> list[str]:
    return [
        f"### {title}",
        "",
        "| Predicted | Count | Mean predicted | Observed rate |",
        "| --- | --- | --- | --- |",
        *[
            f"| {b.label} | {b.count} | {_num(b.mean_predicted)} | {_num(b.observed_rate)} |"
            for b in buckets
        ],
        "",
        f"ECE: {_num(error)}",
    ]


def render_calibration_report(
    sections: Sequence[Section],
    *,
    readings: int,
    unlabeled: int,
    builds: Mapping[str, int],
    written_on: date,
) -> str:
    lines = [
        "# Jev calibration report (spec 03, information only)",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Written on | {written_on.isoformat()} |",
        f"| Readings | {readings} |",
        f"| Labeled | {sections[0].samples if sections else 0} |",
        f"| Not labeled (no price 5 sessions after the legal close) | {unlabeled} |",
        f"| Resolved builds | {', '.join(f'{b} ({n})' for b, n in sorted(builds.items()))} |",
        "",
        (
            "Label: the symbol's adj-close return minus QQQ's, from the document's legal close to "
            "5 sessions later. Above +2% is `up`, below -2% is `down`, otherwise `flat`. Jev's "
            "training cutoff is unpublished, so results before 2026-09-15 may be optimistic."
        ),
        "",
        (
            "Nothing is adjusted automatically. Changing theta or the 0.70 positive threshold "
            "after reading this report is an owner decision recorded in the spec 03 changelog."
        ),
    ]
    for section in sections:
        counts = ", ".join(f"{label} {n}" for label, n in section.label_counts.items())
        lines += [
            "",
            f"## {section.name} ({section.samples} documents)",
            "",
            f"Labels: {counts}.",
            "",
            *_table("p_positive vs up", section.positive, section.positive_ece),
            "",
            *_table("p_negative vs down", section.negative, section.negative_ece),
            "",
            (
                f"Argmax impact accuracy: {_pct(section.accuracy)} (majority baseline "
                f"`{section.majority_label}`: {_pct(section.majority_rate)})"
            ),
        ]
    return "\n".join(lines) + "\n"
