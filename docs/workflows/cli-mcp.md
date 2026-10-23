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
