# Fresh STM32 KLE → PCB → QMK replay

This experimental source-checkout workflow uses `ansi61` and `compact68`.
The current candidate has one accepted fresh build of each, but its repeated
68-key trial failed with five connections remaining. Repeated qualification
has not passed; see the [measured results](../qualification/0.2.0rc1.json).
Run commands from the checkout root. Existing routed keyboards are never cold
inputs.

## External tools

Install SmilePCB as described in the root README, including `.[test]`.
The tested stack is macOS arm64, KiCad 10.0.7, Java 25, Freerouting 2.5.0,
a local QMK tree with required submodules, QMK CLI, ARM GCC and `dfu-suffix`.
Provisioning is manual in this version; no toolchain or binary is bundled.

Use the [KiCad downloads](https://www.kicad.org/download/),
[Freerouting release](https://github.com/freerouting/freerouting/releases/tag/v2.5.0)
and [QMK setup documentation](https://docs.qmk.fm/newbs_getting_started).

The Freerouting JAR SHA-256 is enforced:

```text
f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7
```

Copy [tools.example.json](../../examples/tools.example.json) to
`.lab/tools.json` and replace placeholders. All eight keys are required.
Relative paths resolve against the configuration file. `compiler_libraries`
may be empty when the compiler needs no extra dynamic-library directories.

```sh
mkdir -p .lab
cp examples/tools.example.json .lab/tools.json
# Edit .lab/tools.json with your actual paths before continuing.
.venv/bin/python -c "from smilepcb.keyboard_tools import load_tools; load_tools('.lab/tools.json'); print('Tool paths and JAR checksum OK')"
```

QMK builds use isolated outputs with two jobs; the original source tree is not
edited. The candidate toolchain uses QMK commit
`98e65cf3e1fb4bf61367d469a62d3cce0a9fac20` with its pinned submodules and
QMK CLI 1.1.8. Use that revision when provisioning the source tree:

```sh
git clone https://github.com/qmk/qmk_firmware.git /path/to/qmk_firmware
git -C /path/to/qmk_firmware checkout --detach 98e65cf3e1fb4bf61367d469a62d3cce0a9fac20
git -C /path/to/qmk_firmware submodule update --init --recursive
```

Build receipts fingerprint the isolated runtime's actual source files. They
set `runtime_commit_verified: false` because that copy has no Git metadata;
this flag does not verify the upstream revision. A different QMK version
requires a new compatibility check.

## Public controller source

Fetch the pinned public Le Chiffre donor into ignored local storage:

```sh
mkdir -p .lab/upstream .lab/public-controller/sources/le-chiffre
git clone https://github.com/sporkus/le_chiffre_keyboard_stm32.git .lab/upstream/le-chiffre
git -C .lab/upstream/le-chiffre checkout --detach 57f64495c4b6dca1566b8ddbd464849c4023a56f
cp .lab/upstream/le-chiffre/kicad/pcb/stm32_hotswap_chiffre.kicad_pcb .lab/public-controller/sources/le-chiffre/
cp .lab/upstream/le-chiffre/COPYING .lab/public-controller/sources/le-chiffre/
cp .lab/upstream/le-chiffre/readme.md .lab/public-controller/sources/le-chiffre/
cp docs/fixtures/le-chiffre-manifest.json .lab/public-controller/sources/le-chiffre/manifest.json
shasum -a 256 .lab/public-controller/sources/le-chiffre/stm32_hotswap_chiffre.kicad_pcb
```

Expected PCB hash:

```text
984473f03db99eda31c3b5f12a3bb2e3d07199ed8e2577d0f6cd78b123639b82
```

The workflow consumes those four copied files. It checks the PCB hash and copies
the donor's CERN-OHL-W-2.0 COPYING, README and provenance into generated projects.

## Run

```sh
.venv/bin/python recipes/stm32/generate.py \
  .lab/tools.json first-ansi61 \
  --fixture ansi61 --native-staged \
  --process-profile fr4-2l-1oz-0p5-0p2 \
  --optimize-matrix-gpio --completion-budget-s 3600 \
  --routing-quality turns
```

Use `--fixture compact68` with a different tag for the second layout.
Inputs live in `recipes/stm32/inputs/`: KLE geometry plus a
`keyboard.json` matrix/keycode/profile sidecar.

The process profile requires 0.15 mm minimum track/clearance, 0.2 mm
copper-to-edge clearance and 0.5/0.2 mm vias (0.15 mm radial annulus).
These are explicit process assumptions, not board-house certification.

Each run extracts and normalizes the donor, finds a legal placement and safe
GPIO mapping, then generates the schematic and firmware model. Native routing
prepares interfaces, control, rows, columns and the RGB snake. If incomplete,
checked global recovery starts from this run's generated seed. Exact
native→DSN→SES→native proofs precede actual global routing. Final native
acceptance, independent connectivity and matching QMK remain mandatory.

The 3600-second option bounds completion, not the whole command. Other stages
also have deadlines. Global routing uses at most two CPUs and a 512 MiB Java
heap; process RSS can exceed the heap. Run one heavy workflow at a time.
Success on another runtime/layout is not guaranteed.

## Routing quality

`--routing-quality legacy` is the default. `--routing-quality turns` is an
opt-in candidate policy for `--native-staged`. It ranks simple legal paths by
physical length plus a 1 mm equivalent cost per direction change. Grid search
keeps different ingress headings as separate labels; exact terminal access
leads count, and a via resets heading on the new face. Costs are positive.
The search is bounded and heuristic, not an optimality guarantee.

Both quality modes share placement, USB preparation, native constraints and
Freerouting's cost model. Native component routing and local recovery capture the
selected policy. A fallback result records `routing_quality` separately from
`global_engine`; it does not imply that Freerouting implements the bend policy.
Whole-copper diagnostics report length, endpoint bends, branches, vias and arcs,
including power and ground. Branch counts use serialized segment endpoints and
can change when the same copper is split into more segments; they are not a
normalized topology metric. These diagnostics never replace native admission
checks.

The new local collision checks include copper board/footprint graphics, text
and fields, and use both nets' effective clearances. Registration targets are
not milled edges. Native custom-rule evaluation remains part of final DRC.
Run optional native regression checks with the configured KiCad interpreter:

```sh
SMILEPCB_KICAD_PYTHON=/path/to/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
  .venv/bin/python -m pytest -q tests/test_native_physical_obstacles.py
```

The candidate did not pass repeated full-board qualification. A successful
individual build does not establish repeatable completion or identical geometry.
The earlier 0.1.0 results do not qualify this new mode.

The staged coordinator does not yet compare both retained routing checkpoints. Its global fallback can replace an incomplete native attempt with one that has more unconnected groups; a failed global job stops the workflow while the native attempt remains available only as diagnostic evidence. This is a known recovery limitation, not evidence that selecting the checkpoint with fewer gaps would complete the board.

## Rule scope

KiCad checks run with `--severity-all` and schematic parity. Accepted projects
have empty DRC exclusion lists. The supplied profile retains these ignored
categories: `footprint_filters_mismatch`, `footprint_type_mismatch`,
`missing_courtyard`, `track_not_centered_on_via` and
`tuning_profile_track_geometries`. Zero findings refers to this explicit
profile, not every possible category being enabled. Review the process profile
against the chosen fabricator and assembly requirements.

## Results

Under `.lab/public-controller/<tag>/`:

- `result.json`: written only when the fresh workflow is accepted.
- `finished/project/`: KiCad PCB/schematics/project, BOM, QMK and provenance.
- `finished/firmware/`: build log, runtime fingerprint and BIN/ELF.
- `finished/`: independent connectivity and native rule evidence.
- Intermediate logs/candidates: retained placement, native and global attempts.

A successful placement diagnostic is not a complete keyboard. Check
`complete: true`, `fresh_from_kle: true`, zero native findings/gaps and
matching clean firmware evidence. Failed commands leave diagnostic artifacts.
The generated boards still require electrical, mechanical and assembly review.


The completion stage retains its original short provisional search. After the
ordinary geometric attempts, a final search may explore up to eight temporary
states inside allocated copies, with at most initial+2 pending groups (capped
at eight). It chooses signal or return recovery from the current native state.
The original completion deadline and final-check reserve still apply. No
intermediate state is promoted: the original frame, all pad partitions, native
rules and matching firmware must pass the normal final acceptance.
