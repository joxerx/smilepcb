# Fresh STM32 KLE → PCB → QMK replay

Version 0.1.0 qualifies `ansi61` and `compact68` using this source-checkout
workflow. Run commands from the checkout root. Existing routed keyboards
are never cold inputs.

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
edited. Qualification used a retained runtime, not a newly verified upstream
checkout. Receipts fingerprint its actual source tree and deliberately set
`runtime_commit_verified: false`. A different QMK version requires a new
compatibility check.

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
  --optimize-matrix-gpio --completion-budget-s 3600
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
