# Installed STM32 KLE → PCB → QMK workflow

The canonical assembly and routing order, including demonstrated evidence and pending integration, is documented in [routing-order.md](routing-order.md).

The development pipeline is available through the installed `smilepcb build`
command and local MCP. It awaits fresh native qualification after the current
routing and packaging changes. The [published0.2.0rc1 measurements](../qualification/0.2.0rc1.json)
cover the earlier `fc08beec` runtime only; they do not qualify this version.

## External tools

Use Python3.11+, KiCad10.0.7 on macOS arm64, Java25, Freerouting2.5.0, QMK CLI1.1.8,
ARM GCC and `dfu-suffix`. Native support on other platforms is unqualified.
Tool provisioning is explicit; no native application, compiler or donor CAD is
bundled or downloaded automatically. The installed command accepts a tools JSON
with exactly these keys:

```json
{
  "kicad_app": "/path/to/KiCad.app",
  "java": "/path/to/java",
  "freerouting_jar": "/path/to/freerouting-2.5.0.jar",
  "qmk_home": "/path/to/qmk_firmware",
  "qmk_bin": "/path/to/qmk",
  "arm_gcc": "/path/to/arm-none-eabi-gcc",
  "compiler_libraries": [],
  "dfu_suffix": "/path/to/dfu-suffix"
}
```

Paths resolve relative to that JSON. `compiler_libraries` may be empty.
The package resource `smilepcb.stm32/tools.example.json` supplies the same template.
The Freerouting JAR SHA256 is enforced:

```text
f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7
```

Provision [KiCad](https://www.kicad.org/download/),
[Freerouting2.5.0](https://github.com/freerouting/freerouting/releases/tag/v2.5.0)
and [QMK](https://docs.qmk.fm/newbs_getting_started) separately. The recorded QMK
source is `98e65cf3e1fb4bf61367d469a62d3cce0a9fac20`; initialize its pinned
submodules. Firmware receipts fingerprint the actual runtime files. A Git-less
runtime copy reports `runtime_commit_verified: false`; that does not independently
verify its upstream revision.

## Explicit public controller source

Select a working directory outside the installation. Fetch the pinned public
Le Chiffre source yourself, and copy its four required files into a dedicated
source directory. For example, from your working directory:

```sh
mkdir -p sources/le-chiffre
git clone https://github.com/sporkus/le_chiffre_keyboard_stm32.git upstream-le-chiffre
git -C upstream-le-chiffre checkout --detach 57f64495c4b6dca1566b8ddbd464849c4023a56f
cp upstream-le-chiffre/kicad/pcb/stm32_hotswap_chiffre.kicad_pcb sources/le-chiffre/
cp upstream-le-chiffre/COPYING upstream-le-chiffre/readme.md sources/le-chiffre/
python - <<'PY'
from importlib.resources import files
from pathlib import Path
Path('sources/le-chiffre/manifest.json').write_bytes(
    (files('smilepcb.stm32') / 'controller-manifest.json').read_bytes())
PY
```

The PCB SHA256 is
`984473f03db99eda31c3b5f12a3bb2e3d07199ed8e2577d0f6cd78b123639b82`.
The manifest, README and COPYING are checked too. Symlinked donor files and
mismatched sources fail before generation. The original source is read-only;
verified copies/provenance and the donor's CERN-OHL-W-2.0 license accompany the
new generated project. This is not permission to substitute private hardware.

## Inputs and run

Provide complete KLE JSON plus a bounded `keyboard.json` sidecar. The schema has
`schema:1`, a name, profile `stm32f072-mx-mini-e-udb-c5`, one safe `KC_...` keycode
per serialized key and Boolean `rgb`. Supported geometry is unrotated, with
bounded MX key sizes; unsupported compound/rotated keys are rejected. The same
model derives matrix cells, physical terminals, final GPIOs, schematics and QMK.

Installed package resources include three example input directories. To copy a
fixture into your working directory without needing a checkout:

```sh
python - <<'PY'
from importlib.resources import files
from pathlib import Path
source = files('smilepcb.stm32') / 'inputs' / 'ansi61'
for name in ('layout.kle.json', 'keyboard.json'):
    Path(name).write_bytes((source / name).read_bytes())
PY
smilepcb build --kle layout.kle.json --config keyboard.json --tools tools.json \
  --controller-source sources/le-chiffre --output build/keyboard \
  --process-profile fr4-2l-1oz-0p5-0p2 --routing-quality turns \
  --completion-budget-s 3600 --placement-attempts 8
```

Use `compact68` or `st40-grid48` when copying the other examples. Keep the ST40
[SOURCES notice](../../smilepcb/stm32/inputs/st40-grid48/SOURCES.md) with redistributed
fixture inputs. It selects48 individual1u positions, omits the optional
 overlapping2u position and disables RGB; generated hardware uses this product's
own recipes and is not the original solder-MX ST40 PCB.

Output must be new and outside the installation/controller-source directory.
All projects, intermediate attempts, checks, firmware, native configuration and
cache files remain below it. The command is independent of repository location
and current working directory. Existing source recipe commands are compatibility
shims to these package-owned stages; they contain no second routing engine.

The profile requires0.15mm minimum track/clearance,0.2mm copper-to-edge clearance
and0.5/0.2mm vias with0.15mm radial annulus. These are explicit process assumptions,
not fabricator certification. `legacy-0p6-0p3` remains an explicit alternative.
The default installed quality policy is `turns`; `legacy` selects the older path
cost. Costs never waive native physical checks.

## Routing and acceptance

The ordinary installed command extracts/normalizes the pinned core, applies the
selected process profile, and searches at most eight poses. Each native pose has
the existing220s preparation and240s worker caps. GPIO allocation, checked source
interfaces, local prerequisites, opposite-face key fields and capacity-aware
collectors construct the **complete physical matrix before composition**.
A short outlet or incomplete matrix rejects the pose. Rows have horizontal trunks;
columns have vertical/45-degree trunks on the opposite preferred face. Native
inline layer bridges remain possible. Feeders have a separate geometry role.

Every numbered matrix terminal and MCU alias is mandatory. Cleanup and native
connectivity precede the fixed receipt. Composition can rebind only declared
schematic paths and exported net labels; recorded copper/terminal geometry is
fixed and checked again. RGB, power, ground and bounded recovery consume that
same contract. A matrix-only result is not a finished board.

The final gate requires native ERC/DRC, schematic parity, native unconnected0,
independent pad-group gaps0, two copper layers and clean matching QMK. Only then
is the top-level `result.json` written with `complete:true` and
`fresh_from_kle:true`. Failed runs retain intermediate diagnostics and
`failure.json`. The accepted KiCad project and firmware/check receipts are under
`finished/`. These software checks do not qualify fabricated hardware.

The completion option bounds completion only. Other stage deadlines remain:
extract90s, normalize90s, compose240s and native routing1800s; completion has its
existing180s outer supervision reserve. Freerouting retains its512MiB heap/two-CPU
limits and QMK uses two compiler jobs. Run one heavy workflow at a time.
Native process RSS can exceed the Java heap. Routing is bounded and heuristic;
no universal completion, identical geometry, optimality or appearance guarantee
is implied.

## Rule scope

KiCad checks use `--severity-all` and schematic parity. Accepted projects have
empty DRC exclusion lists. The explicit profile retains these five ignored
categories: `footprint_filters_mismatch`, `footprint_type_mismatch`,
`missing_courtyard`, `track_not_centered_on_via` and
`tuning_profile_track_geometries`. Zero findings refers to this documented
profile, not all possible categories being enabled. No tolerance or exception is
added to improve the release result.
