# Changelog

Changes are recorded here and on the GitHub Releases page.

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
