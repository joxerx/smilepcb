# SmilePCB

[![Code: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/joxerx/smilepcb?include_prereleases)](https://github.com/joxerx/smilepcb/releases)

**Keyboard layouts → KiCad PCB projects and matching QMK firmware.**

SmilePCB automates placement, GPIO assignment, two-layer routing and native
checks for STM32F072CBT6 keyboards with a USB-C daughterboard connection,
MX hot-swap switches and optional SK6812MINI-E per-key RGB.

Version0.3.0 adds a complete installed KLE build workflow and an opt-in routing
profile with straight row trunks, vertical/45-degree columns on the opposite
copper face, and grouped controller approaches. See the
[routing guide](docs/reference-style-routing.md) for the profile and limits.

**Release qualification is being repeated before publication.** The final
0.3.0 report will describe only the completed ANSI61 and Compact68 RGB cohort.
The older [0.2.0rc1 report](docs/qualification/0.2.0rc1.json) qualifies its own
runtime only. Software checks do not establish fabricated-hardware validation.

## Install

Python3.11+ is required. Install the wheel from the
[Releases page](https://github.com/joxerx/smilepcb/releases), or install source:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The package contains the complete coordinator, stage workers and fixture inputs.
Native tools and donor CAD are installed separately. The tested toolchain is
macOS arm64, KiCad10.0.7, Java25, Freerouting2.5.0 and pinned QMK/ARM tools.
Follow the [STM32 setup guide](docs/workflows/stm32.md).

## Build from KLE

Supply a KLE file, its matrix/config sidecar, explicit tool paths, the pinned
public donor directory and a new output directory. The parser supports bounded
unrotated MX layouts; it does not accept every KLE feature or controller family.

The reference-style profile used for qualification is explicit:

```sh
smilepcb build --kle ./layout.kle.json --config ./keyboard.json \
  --tools ./tools.json --controller-source ./sources/le-chiffre \
  --output ./build/keyboard \
  --unified-controller-plan --component-corridors --coherent-via-banks \
  --placement-attempts 4 --placement-budget-s 600 \
  --completion-budget-s 600 --routing-quality turns \
  --process-profile fr4-2l-1oz-0p5-0p2 --usb-max-length-mm none
```

`none` disables only the geometric USB length cap. It preserves clearance,
connectivity, pair-skew and the current single-layer/no-via USB checks; it is
not an impedance or signal-integrity proof. The ordinary default remains20mm.
The600-second options bound their stages, not the whole command. Run one heavy
build at a time; heuristic routing does not guarantee completion on any layout.

The package includes ANSI61, Compact68 and a smaller ST40-derived input. Only
layouts explicitly listed in a release report have full-board qualification.
No40%/ANSI104 RGB completion guarantee is made for0.3.0.

## Outputs and acceptance

A successful build retains a KiCad PCB and schematics, BOM, QMK sources and a
clean firmware build, plus native and independent verification receipts under
`finished/`. All generated files and scratch data stay below the chosen output.
Existing outputs are rejected; the donor source is read-only and checksum-pinned.

Acceptance requires the complete matrix, ERC/DRC and schematic parity,
independent saved-board pad connectivity, matching GPIO/key/LED positions and
clean QMK. DRC zero refers to the documented
[five-category rule profile](docs/workflows/stm32.md#rule-scope).
Failed builds retain diagnostics and do not produce an accepted result.

## CLI, MCP and development

`smilepcb-mcp` exposes the same generator through serialized local jobs, with
explicit workspace boundaries and cancellation cleanup. Existing inspect,
preview and encoder operations retain their bounded synthetic profiles.
See [CLI/MCP usage](docs/workflows/cli-mcp.md).

```sh
python -m pip install -e '.[test]' build
python -m pytest -q
python -m build
```

CI checks portable tests, built artifacts and installed entry points outside
the checkout. Native checks require separately selected KiCad/QMK tools.

- [Community and component sources](docs/sources.md)
- [Contributing and issue/PR guidance](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md), [security policy](SECURITY.md)

Original code is MIT. Bundled CAD and layout-derived inputs retain their own
licenses; generated CAD is not automatically MIT. See
[third-party notices](THIRD_PARTY_NOTICES.md).
