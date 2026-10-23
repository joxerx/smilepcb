"""Cancellable, serialized local jobs behind the stdio MCP adapter.

Each job owns a process group. Protocol cancellation terminates native children
and gives the Python transaction a bounded interval to restore modified files.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

import anyio

from . import service
from .circuit import RGB_BLOCK
from . import encoder


def dispatch(request):
    workspace = Path(request['workspace']).resolve(strict=True)
    project = (workspace / request['project']).resolve(strict=True)
    if not project.is_relative_to(workspace):
        raise ValueError('Project path escapes the configured lab workspace')
    operation = request['operation']
    plan = request.get('plan')
    if operation == 'inspect':
        result = service.inspect_project(project)
        return {'rgb_installed': result['rgb_installed'], 'encoder_installed': result.get('encoder_installed', False),
                'footprints': len(result['board']['footprints']), 'tracks_vias': len(result['board']['tracks']),
                'hashes': result['hashes']}
    if operation == 'plan_rgb':
        return service.plan_rgb(project)
    if operation == 'apply_rgb':
        if not isinstance(plan, dict) or plan.get('block') != RGB_BLOCK:
            raise ValueError('Use the matching apply tool for this plan block')
        return service.apply_plan(project, plan)
    if operation == 'plan_encoder':
        return encoder.plan_encoder(project)
    if operation == 'preview_encoder':
        return encoder.preview_plan(project, plan)
    if operation == 'apply_encoder':
        return encoder.apply_encoder(project, plan)
    if operation == 'rollback_encoder':
        return encoder.rollback_encoder(project)
    if operation == 'verify':
        return service.verify_project(project)
    raise ValueError('Unknown SmilePCB job')


class JobRunner:
    """One active native workflow per server, including different projects."""
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve(strict=True)
        self.lock = asyncio.Lock()

    async def run(self, operation, project, plan=None):
        path = (self.workspace / project).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError('Project path escapes the configured lab workspace')
        request = {'workspace': str(self.workspace), 'project': project, 'operation': operation, 'plan': plan}
        async with self.lock:
            # Shield process creation so AnyIO protocol cancellation cannot lose
            # the process handle before the cleanup handler has been installed.
            with anyio.CancelScope(shield=True):
                child = await asyncio.create_subprocess_exec(sys.executable, '-m', 'smilepcb.mcp_jobs',
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True)
            try:
                async with asyncio.timeout(300):
                    stdout, stderr = await child.communicate(json.dumps(request).encode())
            except BaseException:
                with anyio.CancelScope(shield=True):
                    await self.stop(child)
                raise
            if child.returncode:
                try:
                    message = json.loads(stdout)['error']
                except (ValueError, KeyError):
                    message = stderr.decode(errors='replace')[-2000:] or 'SmilePCB worker failed'
                raise ValueError(message)
            return json.loads(stdout)

    @staticmethod
    async def stop(child):
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(child.communicate(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await child.communicate()


def main():
    def cancelled(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt('MCP job cancelled')

    signal.signal(signal.SIGTERM, cancelled)
    try:
        result = dispatch(json.load(sys.stdin))
    except BaseException as error:
        print(json.dumps({'error': str(error) or type(error).__name__}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
