# Phase H — Release candidate & final certification

This file is the certification record for the Production Special-Order
path after Phases C–G. The core stays frozen (`PRODUCTION_SEMANTICS.md`
SF-1..SF-8). No planner, pricing, or order-model edits were made in Phase H.

**Identifier:** package `0.4.0rc1`, git tag `v0.4.0-rc1`  
**Baseline commit (this certification):** recorded on the tagged commit.  
**Machine:** Cloud Agent VM, Python 3.12, `QT_QPA_PLATFORM=offscreen`, tmp SQLite.

---

## H.1 — Full regression audit

Re-run on 2026-09-07 after G.3 was on `main` (`ab246e0`), with only this
documentation/versioning change on the branch.

| Gate | Result |
|---|---|
| Phase C–G files (C, D, E.2–E.5, F.1–F.2, G.2–G.3) | 97 passed |
| `pytest -m release` | **178 passed**, 1017 deselected, 38.25s |
| Full suite | **1195 passed**, 59 existing `datetime.utcnow` warnings, 133.81s |

Compared with the G.3 certification (178 release / 1195 full): **no change
in pass count**, no new failures, no skipped release tests.

Covered by the release marker: core semantics (C/D), persistence and UX
(E.2–E.3), auto-recompute wrapper (E.4), job-slot/cost-index extras (E.5),
cross-feature and operator paths (F), scale semantics (G.1), reliability
(G.2), failure recovery (G.3), plus the rest of the Special-Order
`pytest.mark.release` set (`test_production_special_orders.py`,
invention, GUI/CLI path tests).

**Verdict:** no regression, no new side effects, no semantic drift detected.

---

## H.2 — Performance baseline (G.1 vs re-run)

Command: `pytest tests/test_phase_g1_scale_baseline.py -s`

Pooled `Finished Widget A × 2`, combined = one planner run over qty `2N`.

| N | G.1 create | H.2 create | G.1 list | H.2 list | G.1 one | H.2 one | G.1 combined | H.2 combined | G.1 audit | H.2 audit |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4.67 | 7.11 | 1.25 | 1.25 | 15.89 | 16.02 | 9.07 | 9.15 | 2.28 | 2.28 |
| 10 | 85.01 | 63.24 | 6.33 | 6.26 | 8.86 | 8.99 | 18.52 | 18.63 | 7.07 | 7.15 |
| 50 | 279.35 | 319.88 | 26.99 | 28.02 | 8.81 | 9.09 | 60.24 | 61.60 | 28.39 | 28.60 |
| 100 | 727.75 | 650.12 | 54.25 | 54.34 | 8.78 | 8.84 | 112.81 | 114.61 | 55.21 | 55.60 |

Other points:

| Scenario | G.1 ms | H.2 ms |
|---|---|---|
| One order, 20 distinct types, compute | 115.41 | 116.04 |
| Combined 20 distinct one-item orders | 135.70 | 135.11 |
| Combined 4-level BOM (A+B) | 27.55 | 27.40 |
| Combined T2 10× qty 10 | 35.66 | 35.45 |

**Comparison:** list / one-compute / combined / audit at N=100 differ by
under 2%. Create × N varies more (many short SQLite transactions); both
runs stay the same order of magnitude and the same ranking. No new
bottleneck. Official numbers remain those in `PHASE_G_SCALE.md`; this
table is the H.2 confirmation, not a replacement SLO.

Hangar-netting at scale (G.1 `test_hangar_net_at_scale_does_not_net_top_level`)
still holds: 10 orders × qty 10, Combined net true, top-level runs = 100.

---

## H.3 — Integrity audit

Re-run: G.2, G.3, F.1, F.2, E.2 (included in the 97-test C–G group; all passed).

Confirmed:

- Create with invalid or mixed payloads writes no header and no events.
- Failed Set/Remove / failed auto-recompute wrapper leave items and event
  ids unchanged.
- Exception after header insert in `storage.connect()` rolls back (no
  leftover order).
- Audit reports planted empty headers; does not flag healthy or deleted
  orders as empty/orphan.
- Delete keeps `special_order_events`.
- Combined `net_against_stock` is per-call (SF-5); stored flags unchanged.
- 20 Edit → wrapper Compute → reload → Compute → Combined cycles: one
  `item_set` per Set, hangar ESI stock unchanged, fingerprints stable.
- CLI and GUI read the same stored rows; auto-recompute checkbox is
  session-only after a new view.
- Combined preview is not persisted.

**Extension-point corrections in this phase:** none.

---

## H.5 — Versioning

See `VERSIONING.md`. Package version `0.4.0rc1` (PEP 440). Git tag
`v0.4.0-rc1` marks this Special-Order freeze as a release candidate.
Existing product tags go through `v0.3.2`; `pyproject.toml` was still
`0.2.0` on those tags — this RC is the first time the package version is
aligned with the Special-Order certification.

After the tag: no production-code changes on this identifier. Further
work is a new version.
