# CLI and local MCP

Install the package from a source checkout or a wheel from GitHub Releases.
This version is not published on PyPI. Native operations need the tested macOS
KiCad installation; package installation does not install KiCad.

## Synthetic RGB transaction

Run from the repository root with a new output directory:

```sh
export SMILEPCB_KICAD_APP=/Applications/KiCad/KiCad.app
mkdir -p .lab
.venv/bin/smilepcb demo .lab/carrier
.venv/bin/smilepcb plan-rgb .lab/carrier --output .lab/rgb-plan.json
.venv/bin/smilepcb apply .lab/carrier --plan .lab/rgb-plan.json
.venv/bin/smilepcb verify .lab/carrier
```

This checks a bounded six-key transaction, preserves original copper and verifies
its structural invariants. The carrier remains partly unrouted: `verify` does
not mean ERC/DRC-clean. To obtain native findings:

```sh
.venv/bin/smilepcb check .lab/carrier --output .lab/carrier-checks
```

`check` returns nonzero for remaining findings. This is distinct from the full
[STM32 recipe](stm32.md), whose acceptance also requires clean firmware.

## MCP connection

Run the stdio server with an absolute workspace path:

```sh
.venv/bin/smilepcb-mcp --workspace "$PWD/.lab"
```

An MCP client's configuration can use the following shape; replace all paths:

```json
{
  "mcpServers": {
    "smilepcb": {
      "command": "/absolute/path/to/smilepcb/.venv/bin/smilepcb-mcp",
      "args": ["--workspace", "/absolute/path/to/smilepcb/.lab"],
      "env": {"SMILEPCB_KICAD_APP": "/Applications/KiCad/KiCad.app"}
    }
  }
}
```

Tools: `inspect`, `plan_add_rgb`, `apply_rgb_plan`, `plan_add_encoder`,
`preview_plan`, `apply_encoder_plan`, `rollback_encoder` and `verify`.
Requests must stay inside the workspace. The server supports bounded synthetic
profiles, not arbitrary imported boards. Encoder operations additionally require
the exact supported fixture state and reject other inputs; the RGB demo is the
self-contained introductory example in this release.

## Fresh STM32 generation in the next candidate

The development installed pipeline adds `smilepcb build` and MCP
`generate_keyboard` using the same complete collector coordinator. Current
changes await fresh native qualification. The already published0.2.0rc1 wheel
has its older core-only scope.

Provision the pinned public donor and external tool config explicitly as in the
[STM32 guide](stm32.md). Put KLE/config/tool JSON and the donor directory inside
your MCP workspace. A generation request uses these parameters:

```json
{
  "kle": "layout.kle.json",
  "config": "keyboard.json",
  "tools": "tools.json",
  "controller_source": "sources/le-chiffre",
  "output": "build/new-keyboard",
  "process_profile": "fr4-2l-1oz-0p5-0p2",
  "routing_quality": "turns",
  "completion_budget_s": 3600,
  "placement_attempts": 8
}
```

All paths resolve within the configured workspace; symlink escapes and existing
output are rejected before starting. The tool does not download sources or flash
hardware. One generation or synthetic transaction job runs at a time. A client
must permit the full bounded build duration: generation supervision covers the
unchanged stage caps, up to7980s with the defaults, rather than the synthetic
transaction's300s limit. Cancellation stops owned stages and lets completion
reap its separately grouped QMK process. A private registration pipe and OS birth
identities keep detached cleanup scoped to this job; a new job cannot start until
owned processes are gone. Unconfirmed cleanup blocks further jobs. Failed
output/diagnostics are retained;
this is not a background resumable job service.

Only a final native-rule/connectivity/matching-QMK result is complete. The same
software-versus-hardware and layout limitations in the STM32 guide apply.
