# Jev calibration report (spec 03, information only)

| Field | Value |
| --- | --- |
| Written on | 2026-09-28 |
| Readings | 85318 |
| Labeled | 82367 |
| Not labeled (no symbol or QQQ price at the legal close or 5 sessions later) | 2951 |
| Resolved builds | typesafe/jev-1.13-20260917 (85318) |

Label: the symbol's adj-close return minus QQQ's, from the document's legal close to 5 sessions later. Above +2% is `up`, below -2% is `down`, otherwise `flat`. Jev's training cutoff is unpublished, so results before 2026-09-15 may be optimistic.

Nothing is adjusted automatically. Changing theta or the 0.70 positive threshold after reading this report is an owner decision recorded in the spec 03 changelog.

## All documents (82367 documents)

Labels: down 29247, flat 29346, up 23774.

### p_positive vs up

| Predicted | Count | Mean predicted | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 44090 | 0.015 | 0.285 |
| 0.1-0.2 | 6175 | 0.141 | 0.298 |
| 0.2-0.3 | 4206 | 0.242 | 0.287 |
| 0.3-0.4 | 3201 | 0.343 | 0.279 |
| 0.4-0.5 | 2840 | 0.444 | 0.297 |
| 0.5-0.6 | 2611 | 0.546 | 0.288 |
| 0.6-0.7 | 2656 | 0.645 | 0.285 |
| 0.7-0.8 | 2900 | 0.746 | 0.279 |
| 0.8-0.9 | 3600 | 0.848 | 0.293 |
| 0.9-1.0 | 10088 | 0.971 | 0.301 |

ECE: 0.309

### p_negative vs down

| Predicted | Count | Mean predicted | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 60687 | 0.011 | 0.355 |
| 0.1-0.2 | 4238 | 0.140 | 0.358 |
| 0.2-0.3 | 2654 | 0.242 | 0.352 |
| 0.3-0.4 | 2016 | 0.344 | 0.365 |
| 0.4-0.5 | 1752 | 0.443 | 0.357 |
| 0.5-0.6 | 1550 | 0.545 | 0.348 |
| 0.6-0.7 | 1579 | 0.646 | 0.352 |
| 0.7-0.8 | 1668 | 0.746 | 0.337 |
| 0.8-0.9 | 1986 | 0.846 | 0.349 |
| 0.9-1.0 | 4237 | 0.966 | 0.359 |

ECE: 0.332

Argmax impact accuracy: 34.1% (majority baseline `flat`: 35.6%)

## Legal close before 2026-09-15 (80808 documents)

Labels: down 28282, flat 29155, up 23371.

### p_positive vs up

| Predicted | Count | Mean predicted | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 43249 | 0.015 | 0.285 |
| 0.1-0.2 | 6070 | 0.141 | 0.299 |
| 0.2-0.3 | 4118 | 0.242 | 0.287 |
| 0.3-0.4 | 3138 | 0.343 | 0.280 |
| 0.4-0.5 | 2774 | 0.444 | 0.300 |
| 0.5-0.6 | 2558 | 0.546 | 0.287 |
| 0.6-0.7 | 2592 | 0.645 | 0.286 |
| 0.7-0.8 | 2840 | 0.746 | 0.280 |
| 0.8-0.9 | 3541 | 0.848 | 0.295 |
| 0.9-1.0 | 9928 | 0.971 | 0.303 |

ECE: 0.309

### p_negative vs down

| Predicted | Count | Mean predicted | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 59624 | 0.011 | 0.350 |
| 0.1-0.2 | 4134 | 0.140 | 0.354 |
| 0.2-0.3 | 2591 | 0.242 | 0.344 |
| 0.3-0.4 | 1970 | 0.344 | 0.363 |
| 0.4-0.5 | 1726 | 0.443 | 0.353 |
| 0.5-0.6 | 1515 | 0.545 | 0.340 |
| 0.6-0.7 | 1540 | 0.646 | 0.347 |
| 0.7-0.8 | 1624 | 0.746 | 0.329 |
| 0.8-0.9 | 1943 | 0.846 | 0.346 |
| 0.9-1.0 | 4141 | 0.966 | 0.358 |

ECE: 0.328

Argmax impact accuracy: 34.4% (majority baseline `flat`: 36.1%)

## Legal close on or after 2026-09-15 (1559 documents)

Labels: down 965, flat 191, up 403.

### p_positive vs up

| Predicted | Count | Mean predicted | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 841 | 0.015 | 0.273 |
| 0.1-0.2 | 105 | 0.136 | 0.276 |
| 0.2-0.3 | 88 | 0.241 | 0.273 |
| 0.3-0.4 | 63 | 0.342 | 0.222 |
| 0.4-0.5 | 66 | 0.441 | 0.182 |
| 0.5-0.6 | 53 | 0.550 | 0.358 |
| 0.6-0.7 | 64 | 0.644 | 0.234 |
| 0.7-0.8 | 60 | 0.740 | 0.217 |
| 0.8-0.9 | 59 | 0.843 | 0.203 |
| 0.9-1.0 | 160 | 0.968 | 0.219 |

ECE: 0.311

### p_negative vs down

| Predicted | Count | Mean predicted | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 1063 | 0.013 | 0.656 |
| 0.1-0.2 | 104 | 0.139 | 0.538 |
| 0.2-0.3 | 63 | 0.243 | 0.667 |
| 0.3-0.4 | 46 | 0.339 | 0.435 |
| 0.4-0.5 | 26 | 0.453 | 0.615 |
| 0.5-0.6 | 35 | 0.551 | 0.686 |
| 0.6-0.7 | 39 | 0.648 | 0.564 |
| 0.7-0.8 | 44 | 0.745 | 0.636 |
| 0.8-0.9 | 43 | 0.843 | 0.488 |
| 0.9-1.0 | 96 | 0.966 | 0.406 |

ECE: 0.540

Argmax impact accuracy: 22.1% (majority baseline `down`: 61.9%)
