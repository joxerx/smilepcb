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

from .circuit import RGB_BLOCK
from .usb_policy import resolve_usb_max_length_nm



_GENERATION_PATHS=('kle','config','tools','output','controller_source')
_GENERATION_OPTIONS={'process_profile','routing_quality','completion_budget_s','placement_attempts','placement_budget_s','usb_max_length_nm','coherent_via_banks','unified_controller_plan','component_corridors'}


def generation_timeout(payload):
    budget=payload.get('completion_budget_s',3600);attempts=payload.get('placement_attempts',8)
    if type(budget) is not int or not 120<=budget<=3600:raise ValueError('Completion budget must be 120..3600 seconds')
    if type(attempts) is not int or not 1<=attempts<=8:raise ValueError('Placement attempts must be 1..8')
    placement=payload.get('placement_budget_s',220)
    if type(placement) is not int or not 220<=placement<=1200:raise ValueError('Placement budget must be 220..1200 seconds')
    return 2460+(placement+20)*attempts+budget


def generation_arguments(workspace,payload):
    if not isinstance(payload,dict) or not set(_GENERATION_PATHS)<=set(payload) or set(payload)-set(_GENERATION_PATHS)-_GENERATION_OPTIONS:
        raise ValueError('Generation requires explicit workspace input/output/source paths')
    generation_timeout(payload)
    resolve_usb_max_length_nm(payload.get('usb_max_length_nm',...))
    for name in ('coherent_via_banks','unified_controller_plan','component_corridors'):
        if name in payload and type(payload[name]) is not bool:raise ValueError(name+' must be boolean')
    if payload.get('component_corridors') and not payload.get('unified_controller_plan'):raise ValueError('Component corridors require unified controller planning')
    paths={}
    for name in _GENERATION_PATHS:
        value=payload[name]
        if not isinstance(value,str) or not value or '\x00' in value:raise ValueError('Invalid generation workspace path: '+name)
        raw=workspace/value;path=raw.resolve()
        if not path.is_relative_to(workspace):raise ValueError('Generation path escapes the configured workspace')
        if name=='output':
            if raw.exists() or raw.is_symlink():raise ValueError('Select a new output directory')
        elif name=='controller_source':
            if not path.is_dir():raise ValueError('Controller source must be a workspace directory')
        elif not path.is_file():raise ValueError('Generation input must be a workspace file: '+name)
        paths[name]=path
    options={name:payload[name] for name in _GENERATION_OPTIONS if name in payload}
    return tuple(paths[name] for name in ('kle','config','tools','output')),dict(options,controller_source=paths['controller_source'])

def dispatch(request):
    workspace = Path(request['workspace']).resolve(strict=True)
    operation = request['operation']
    if operation=='generate_keyboard':
        args,options=generation_arguments(workspace,request.get('generation'))
        from .stm32.generate import generate_keyboard
        return generate_keyboard(*args,**options)
    from . import service,encoder
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
        self._unclean = None

    async def run(self, operation, project, plan=None):
        path = (self.workspace / project).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError('Project path escapes the configured lab workspace')
        request = {'workspace': str(self.workspace), 'project': project, 'operation': operation, 'plan': plan}
        return await self._run(request,300)

    async def generate(self,payload):
        generation_arguments(self.workspace,payload)
        request={'workspace':str(self.workspace),'operation':'generate_keyboard','generation':payload}
        return await self._run(request,generation_timeout(payload))

    async def _run(self,request,timeout_s):
        from .process_ownership import ProcessGroups
        async with self.lock:
            if self._unclean is not None:
                old_child,old_groups=self._unclean
                await self._finish_cleanup(old_child,old_groups)
                old_groups.close();self._unclean=None
            groups=ProcessGroups();child=None;failure=None;stdout=stderr=b''
            try:
                with anyio.CancelScope(shield=True):
                    package_root=Path(__file__).resolve().parents[1]
                    environment=groups.environment()
                    dependencies=[str(Path(p).resolve()) for p in environment.get('PYTHONPATH','').split(os.pathsep) if p]
                    environment['PYTHONPATH']=os.pathsep.join(dict.fromkeys([str(package_root),*dependencies]))
                    environment['PYTHONDONTWRITEBYTECODE']='1'
                    child = await asyncio.create_subprocess_exec(sys.executable, '-P', '-m', 'smilepcb.mcp_jobs',
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        start_new_session=True,cwd=package_root,env=environment,pass_fds=(groups.write_fd,))
                groups.watch_root(child.pid)
                async with asyncio.timeout(timeout_s):
                    stdout,stderr=await child.communicate(json.dumps(request).encode())
            except BaseException as error:failure=error
            try:
                interrupted=await self._finish_cleanup(child,groups)
            except BaseException:
                self._unclean=(child,groups)
                self._cleanup_receipt(request,child,groups,False)
                raise
            else:
                self._cleanup_receipt(request,child,groups,True)
                groups.close()
            if failure is not None:raise failure
            if interrupted:raise asyncio.CancelledError
            if child.returncode:
                try:message=json.loads(stdout)['error']
                except (ValueError,KeyError):message=stderr.decode(errors='replace')[-2000:] or 'SmilePCB worker failed'
                raise ValueError(message)
            return json.loads(stdout)

    async def _finish_cleanup(self,child,groups):
        # A second native asyncio cancellation must not interrupt the drain.
        interrupted=False
        with anyio.CancelScope(shield=True):
            cleanup=asyncio.create_task(self.stop(child,groups))
            while True:
                try:
                    await asyncio.shield(cleanup)
                    return interrupted
                except asyncio.CancelledError:
                    if cleanup.cancelled():raise RuntimeError('Owned cleanup task was cancelled; new jobs are blocked')
                    interrupted=True

    def _cleanup_receipt(self,request,child,groups,clean):
        if request.get('operation')!='generate_keyboard':return
        path=(self.workspace/request['generation']['output']).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_dir():return
        (path/'process-cleanup.json').write_text(json.dumps({'root_reaped':child is None or child.returncode is not None,
            'all_owned_groups_gone':clean,'registered_detached_events':groups.records},indent=2)+'\n')

    @staticmethod
    async def stop(child,groups):
        groups.signal(signal.SIGTERM)
        if child is not None and child.returncode is None:
            try:await asyncio.wait_for(child.communicate(),timeout=6)
            except asyncio.TimeoutError:
                groups.signal(signal.SIGKILL)
                await asyncio.wait_for(child.communicate(),timeout=2)
        await groups.cleanup()


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
