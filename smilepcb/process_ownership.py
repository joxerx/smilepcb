"""Private per-job process-group registration, never filesystem PID discovery.

Only trusted Python parents inherit the pipe. Detached executables receive no
registration descriptor or environment marker. Birth identities guard PID reuse.
"""
import asyncio
import ctypes
import errno
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

GROUP_FD='SMILEPCB_OWNED_GROUP_FD'


class _BSDInfo(ctypes.Structure):
    # Darwin SDK sys/proc_info.h: struct proc_bsdinfo (PROC_PIDTBSDINFO=3).
    _fields_=[(name,ctypes.c_uint32) for name in ('flags','status','xstatus','pid','ppid','uid','gid','ruid','rgid','svuid','svgid','reserved')]+[
        ('comm',ctypes.c_char*16),('name',ctypes.c_char*32)]+[(name,ctypes.c_uint32) for name in ('nfiles','pgid','jobc','tdev','tpgid')]+[
        ('nice',ctypes.c_int32),('start_seconds',ctypes.c_uint64),('start_microseconds',ctypes.c_uint64)]


@lru_cache(maxsize=1)
def _libproc():
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    library.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
    library.proc_pidinfo.restype=ctypes.c_int
    library.proc_listpids.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_int]
    library.proc_listpids.restype=ctypes.c_int
    return library


def process_identity(pid):
    if sys.platform=='darwin':
        info=_BSDInfo();ctypes.set_errno(0)
        size=_libproc().proc_pidinfo(pid,3,0,ctypes.byref(info),ctypes.sizeof(info))
        if size==0 and ctypes.get_errno() in (0,errno.ESRCH,errno.ENOENT):return None
        if size!=ctypes.sizeof(info):raise OSError(ctypes.get_errno(),'Cannot establish process birth identity')
        return {'birth':[info.start_seconds,info.start_microseconds],'pgid':info.pgid,'ppid':info.ppid}
    if sys.platform.startswith('linux'):
        try:text=Path('/proc',str(pid),'stat').read_text()
        except OSError as error:
            if error.errno in (errno.ESRCH,errno.ENOENT):return None
            raise
        fields=text[text.rfind(')')+2:].split()
        return {'birth':[int(fields[19])],'pgid':int(fields[2]),'ppid':int(fields[1])}
    raise RuntimeError('Owned process cleanup requires macOS or Linux birth identities')


def _darwin_zombie_only_group(pgid,*,deadline=None):
    """Confirm EPERM is Darwin's unreaped-zombie state; never declare it gone.

    proc_pidinfo omits zombies. Compare two bounded, group-specific libproc
    snapshots with ps state, and fail closed if any member is live or unknown.
    """
    if sys.platform!='darwin':return False
    def members():
        values=(ctypes.c_int*4096)()
        size=_libproc().proc_listpids(2,pgid,values,ctypes.sizeof(values))
        if size<=0 or size>=ctypes.sizeof(values) or size%ctypes.sizeof(ctypes.c_int):return None
        result=list(values[:size//ctypes.sizeof(ctypes.c_int)])
        if any(pid<=1 for pid in result) or len(result)!=len(set(result)):return None
        return set(result)
    try:
        before=members()
        if not before:return False
        timeout=.5 if deadline is None else min(.5,deadline-time.monotonic())
        if timeout<=0:return False
        result=subprocess.run(['/bin/ps','-g',str(pgid),'-o','pid=,pgid=,stat='],
            capture_output=True,text=True,timeout=timeout,check=True,env={'LC_ALL':'C'})
        observed=set()
        for row in result.stdout.splitlines():
            pid,group,state=row.split()
            pid=int(pid)
            if int(group)!=pgid or not state.startswith('Z') or pid in observed:return False
            observed.add(pid)
        return observed==before==members() and all(process_identity(pid) is None for pid in observed)
    except (OSError,ValueError,subprocess.SubprocessError):return False


def inherited_group_fds():
    value=os.environ.get(GROUP_FD)
    if value is None:return ()
    try:fd=int(value)
    except ValueError as error:raise ValueError('Invalid private process-registration descriptor') from error
    if fd<3 or not stat.S_ISFIFO(os.fstat(fd).st_mode):raise ValueError('Process registration requires its inherited private pipe')
    return (fd,)


def detached_environment(environment):
    result=dict(environment);result.pop(GROUP_FD,None)
    return result


def _record(action,pid,identity):
    descriptors=inherited_group_fds()
    if not descriptors:return
    data=json.dumps({'action':action,'pid':pid,'identity':identity},separators=(',',':')).encode()+b'\n'
    if len(data)>1024:raise ValueError('Process ownership record exceeds its atomic bound')
    if os.write(descriptors[0],data)!=len(data):raise RuntimeError('Incomplete process ownership registration')


def register_detached_group(pid):
    if not inherited_group_fds():return None
    identity=process_identity(pid)
    if identity is None or identity['pgid']!=pid or identity['ppid']!=os.getpid():
        raise RuntimeError('Detached process is not a newly owned child group')
    _record('start',pid,identity)
    return identity


def unregister_detached_group(pid,identity):
    if identity is None:return
    try:os.killpg(pid,0)
    except ProcessLookupError:_record('stop',pid,identity)


class ProcessGroups:
    def __init__(self):
        self.read_fd,self.write_fd=os.pipe();os.set_blocking(self.read_fd,False)
        self.groups={};self.buffer=b'';self.records=0

    def environment(self):return dict(os.environ,**{GROUP_FD:str(self.write_fd)})

    def watch_root(self,pid):
        identity=process_identity(pid)
        if identity is None or identity['pgid']!=pid or identity['ppid']!=os.getpid():
            raise RuntimeError('MCP worker is not an owned process group')
        self.groups[pid]=identity

    def drain(self):
        while True:
            try:chunk=os.read(self.read_fd,65536)
            except BlockingIOError:break
            if not chunk:break
            self.buffer+=chunk
            if len(self.buffer)>65536:raise RuntimeError('Process registration stream exceeds its bound')
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1);record=json.loads(line)
            pid=record.get('pid');identity=record.get('identity');action=record.get('action')
            if (type(pid) is not int or not 1<pid<2**31 or pid==os.getpgrp() or
                not isinstance(identity,dict) or identity.get('pgid')!=pid or
                not isinstance(identity.get('birth'),list) or len(identity['birth']) not in (1,2) or
                any(type(n) is not int or n<0 for n in identity['birth']) or action not in ('start','stop')):
                raise RuntimeError('Invalid private process ownership record')
            self.records+=1
            if action=='start':
                if pid in self.groups and self.groups[pid]['birth']!=identity['birth']:
                    raise RuntimeError('Process group identity changed during registration')
                self.groups[pid]=identity
            elif self.groups.get(pid,{}).get('birth')==identity['birth']:self.groups.pop(pid)

    def _operate_group(self,pid,expected,signum,deadline):
        # Exit can briefly make both killpg and the zombie snapshots ambiguous.
        # Retry only inside the caller's shared finite budget. Every syscall
        # gets a fresh birth check; only ESRCH removes ownership from the ledger.
        for attempt in range(6):
            current=process_identity(pid)
            if current is not None and current['birth']!=expected['birth']:
                raise RuntimeError('Owned process ID was reused; refusing to signal its replacement')
            try:os.killpg(pid,signum)
            except ProcessLookupError:
                self.groups.pop(pid,None);return
            except PermissionError:
                if _darwin_zombie_only_group(pid,deadline=deadline):return
                left=deadline-time.monotonic()
                if attempt==5 or left<=0:raise
                time.sleep(min(.02,left))
            else:return

    def remaining(self,*,deadline=None):
        stop=time.monotonic()+.1
        if deadline is not None:stop=min(stop,deadline)
        self.drain()
        for pid,expected in list(self.groups.items()):
            self._operate_group(pid,expected,0,stop)
        return sorted(self.groups)

    def signal(self,signum,*,deadline=None):
        stop=time.monotonic()+.1
        if deadline is not None:stop=min(stop,deadline)
        for pid in self.remaining(deadline=stop):
            self._operate_group(pid,self.groups[pid],signum,stop)

    async def cleanup(self,term_grace=.5,kill_grace=2):
        for signum,budget in ((signal.SIGTERM,term_grace),(signal.SIGKILL,kill_grace)):
            deadline=time.monotonic()+budget
            self.signal(signum,deadline=deadline)
            while time.monotonic()<deadline and self.remaining(deadline=deadline):
                left=deadline-time.monotonic()
                if left>0:await asyncio.sleep(min(.02,left))
            self.drain()
            if not self.groups:return
        raise RuntimeError('Owned process groups remain; new jobs are blocked')

    def close(self):
        os.close(self.read_fd);os.close(self.write_fd)
