# Changelog

Changes are recorded here and on the GitHub Releases page.

## 0.2.0rc1 — candidate (2026-10-31)

Experimental candidate. Follow-up fixes are awaiting fresh qualification.
The earlier measured cohort did not pass repeated full-board qualification: fresh
61-key and 68-key builds reached ERC/DRC/connectivity zero with clean matching
QMK, but a second 68-key build stopped with five connections remaining. The
fourth planned trial was not run. This candidate does not establish repeatable
completion. See [measurements](docs/qualification/0.2.0rc1.json).

The staged coordinator still does not rank global and native checkpoints by
future completion difficulty. A failed global job now retains a physically
checked native checkpoint when one exists; final acceptance remains mandatory.
See the [workflow limitations](docs/workflows/stm32.md#routing-quality).

- Make native component ordering depend on conductor geometry rather than
  random identities or iteration order.
- Preserve failed-attempt history across ordinary and provisional recovery.
- Allow the final owned elastic search to explore lower-displacement-cost
  candidates without relaxing physical rules or admitting partial results.
- In turns mode, balance displacement cost and heuristic guidance to avoid
  cutting completed connections when a cheaper bypass is available.

- Index native copper artwork, text and footprint fields in both local
  collision oracles; exclude registration targets from physical milling edges.
- Apply the routed net's effective clearance as well as the obstacle's, including
  spatial searches across bucket boundaries.
- Add opt-in `--routing-quality turns`: positive bend cost, heading-aware grid
  labels, exact terminal leads and via heading reset. Legacy remains default.
- Forward the selected mode through native completion and recovery, and record
  whole-copper diagnostics. Both modes share USB preparation and the external
  global router's cost model.
- Add portable policy/driver and optional real KiCad geometry regressions.
- Reject same-side silkscreen collisions during support-component placement,
  including visible outlines outside otherwise separate courtyards.
- Score all remaining controller-interface constraints and cache conservative
  shape bounds before exact native checks, retaining the same search limits.
- Align generated non-plated-hole clearance with the native rule and the
  conservative DSN exchange geometry.
- Retain checked orthogonal/45-degree off-grid access geometry through DSN-safe
  grid search, so the exact exchange does not move a free-angle terminal lead.
- Disable fanout, autorouting and optimization explicitly during exchange
  preflight, so enabling the actual job's fanout cannot alter its proof input.
- Add a final bounded provisional search after geometric recovery: temporary
  topology exchanges, role-based return recovery and zero-only checked promotion
  within the existing completion deadline.
- Retire redundant completed-net interface tails before geometric recovery,
  retaining unfinished interfaces and exact native pad partitions.
- Reuse connected local return vias across preparation stages, retaining radius
  and solder-land guards instead of overlaying a second escape.
- Prefer actual signal contacts when packing controller supports.
- Try coupled, native-checked 45-degree USB joins before the existing fallback;
  retain source copper and the existing recipe length/skew envelope.

## 0.1.0 — 2026-10-23

Initial experimental developer release.

- Fresh ANSI61 and compact68 KLE workflows using STM32F072CBT6, MX hot-swap,
  Mini-E RGB, a USB-C daughterboard and two copper layers.
- Geometry-based placement/GPIO assignment, ordered native routing and bounded
  global recovery with exact exchange proofs.
- Native ERC/DRC, schematic parity, independent connectivity and matching clean
  QMK acceptance. Both supplied fresh layouts pass the recorded software gate.
- Local CLI/MCP transaction workflows for the supported synthetic profiles.
- Installation/reproduction guides, community-backed recipe references,
  contributor templates, lightweight unit/package CI and third-party notices.
- MIT license for original project code; bundled CAD assets retain CERN-OHL-P.

Hardware has not been fabricated or validated. The complete qualified workflow
requires a source checkout, not a general-purpose MCP keyboard generator.
See the supported scope and limitations in the [README](README.md).
