"""Local-only stdio MCP adapter. A fixed workspace bounds every tool path."""
import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .mcp_jobs import JobRunner


def build_server(workspace):
    workspace = Path(workspace).resolve(strict=True)
    server = FastMCP("SmilePCB Lab", instructions="Synthetic lab projects only. Plan and preview before applying. "
                     "Encoder planning prepares and routes an isolated candidate with bounded offline tools. "
                     "Apply uses that exact candidate. No project discovery, downloads or hardware flashing.")

    jobs = JobRunner(workspace)

    @server.tool(annotations={"readOnlyHint": True})
    async def inspect(project: str) -> dict:
        """Inspect a synthetic lab project without changing it."""
        return await jobs.run('inspect', project)

    @server.tool(annotations={"readOnlyHint": True})
    async def plan_add_rgb(project: str) -> dict:
        """Plan six per-key RGB LEDs, the data buffer, power components and QMK mapping."""
        return await jobs.run('plan_rgb', project)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    async def apply_rgb_plan(project: str, plan: dict) -> dict:
        """Apply a previously returned plan after checking source conflicts and native CAD consistency."""
        return await jobs.run('apply_rgb', project, plan)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def plan_add_encoder(project: str) -> dict:
        """Prepare an exact encoder candidate on a copy of the supported synthetic 3x4.

        Writes only the project's internal plan cache; CAD inputs stay unchanged.
        Includes bounded routing, native checks, GPIO/placement and file diffs.
        """
        return await jobs.run('plan_encoder', project)

    @server.tool(annotations={"readOnlyHint": True})
    async def preview_plan(project: str, plan: dict) -> dict:
        """Show prepared encoder changes and whether project inputs still match."""
        return await jobs.run('preview_encoder', project, plan)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    async def apply_encoder_plan(project: str, plan: dict) -> dict:
        """Apply the exact reviewed candidate; restore original files if commit fails."""
        return await jobs.run('apply_encoder', project, plan)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    async def rollback_encoder(project: str) -> dict:
        """Undo this encoder operation only if no later input edits would be lost."""
        return await jobs.run('rollback_encoder', project)

    @server.tool(annotations={"readOnlyHint": True})
    async def verify(project: str) -> dict:
        """Export a real KiCad netlist and verify PCB pins and QMK LED order."""
        return await jobs.run('verify', project)

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    build_server(args.workspace).run(transport="stdio")


if __name__ == "__main__":
    main()
