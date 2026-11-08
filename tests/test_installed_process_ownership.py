"""OS identity/pipe and compiler-boundary checks without native applications."""
import errno
import os
from pathlib import Path
import signal

import pytest
from smilepcb import process_ownership as ownership


def test_process_identity_has_a_stable_birth_and_actual_group():
    first=ownership.process_identity(os.getpid())
    assert first==ownership.process_identity(os.getpid())
    assert first['pgid']==os.getpgrp() and first['ppid']==os.getppid()


def test_group_pid_reuse_never_signals_the_replacement(monkeypatch):
    groups=ownership.ProcessGroups();calls=[]
    try:
        groups.groups[42]={'birth':[1,2],'pgid':42,'ppid':os.getpid()}
        monkeypatch.setattr(ownership,'process_identity',lambda _: {'birth':[9,9],'pgid':42,'ppid':os.getpid()})
        monkeypatch.setattr(ownership.os,'killpg',lambda *args:calls.append(args))
        with pytest.raises(RuntimeError,match='reused'):groups.signal(signal.SIGKILL)
        assert calls==[]
    finally:groups.close()


def test_registration_rejects_a_filesystem_descriptor(tmp_path,monkeypatch):
    path=tmp_path/'not-a-private-pipe';fd=os.open(path,os.O_CREAT|os.O_WRONLY,0o600)
    monkeypatch.setenv(ownership.GROUP_FD,str(fd))
    try:
        with pytest.raises(ValueError,match='pipe'):ownership.inherited_group_fds()
    finally:os.close(fd)


def test_actual_compiler_spawn_does_not_receive_registration_authority(tmp_path,monkeypatch):
    from smilepcb import keyboard_firmware as firmware
    groups=ownership.ProcessGroups();monkeypatch.setenv(ownership.GROUP_FD,str(groups.write_fd))
    project=tmp_path/'project';(project/'qmk').mkdir(parents=True);(project/'qmk/config.h').write_text('test')
    source=tmp_path/'qmk-source';(source/'layouts/default').mkdir(parents=True);(source/'Makefile').write_text('not executed')
    binary=tmp_path/'unused-tool';binary.write_text('not executed')
    tools=dict(qmk_home=str(source),qmk_bin=str(binary),arm_gcc=str(binary),compiler_libraries=[],dfu_suffix=str(binary))
    class SpawnBoundary(Exception):pass
    def stopped(command,**kwargs):
        assert ownership.GROUP_FD not in kwargs['env']
        assert not kwargs.get('pass_fds')
        raise SpawnBoundary
    monkeypatch.setattr(firmware.subprocess,'Popen',stopped)
    try:
        with pytest.raises(SpawnBoundary):firmware.compile_qmk(project,tmp_path/'output',tools)
    finally:groups.close()


def test_leaderless_owned_group_is_drained_while_grandchild_remains(tmp_path):
    import asyncio,json,subprocess,sys,time
    marker=tmp_path/'grandchild.json'
    code="""import json,subprocess,sys
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
marker=Path(sys.argv[1]);temporary=marker.with_suffix('.tmp')
temporary.write_text(json.dumps({'grandchild':child.pid}));temporary.replace(marker)
sys.stdin.buffer.read(1)
"""
    leader=subprocess.Popen([sys.executable,'-c',code,str(marker)],stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    groups=ownership.ProcessGroups()
    try:
        deadline=time.monotonic()+5
        while not marker.exists():
            assert leader.poll() is None
            assert time.monotonic()<deadline
            time.sleep(.01)
        grandchild=json.loads(marker.read_text())['grandchild']
        groups.watch_root(leader.pid)
        leader.stdin.write(b'x');leader.stdin.flush();leader.wait(timeout=2)
        assert ownership.process_identity(leader.pid) is None
        assert os.getpgid(grandchild)==leader.pid
        assert groups.remaining()==[leader.pid]
        asyncio.run(groups.cleanup())
        assert groups.remaining()==[]
        with pytest.raises(ProcessLookupError):os.kill(grandchild,0)
    finally:
        try:
            groups.signal(signal.SIGKILL)
            if leader.poll() is None:
                os.killpg(leader.pid,signal.SIGKILL);leader.wait(timeout=2)
        finally:
            leader.stdin.close();groups.close()


@pytest.mark.skipif(os.uname().sysname!='Darwin',reason='Darwin zombie-only groups return EPERM')
def test_zombie_only_permission_race_waits_for_reap_instead_of_accepting_or_failing():
    import asyncio,subprocess,sys,time
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    groups=ownership.ProcessGroups()
    try:
        groups.watch_root(child.pid)
        child.stdin.write(b'x');child.stdin.flush();deadline=time.monotonic()+3
        while True:
            state=subprocess.check_output(['/bin/ps','-p',str(child.pid),'-o','stat='],text=True).strip()
            if state.startswith('Z'):break
            assert time.monotonic()<deadline
            time.sleep(.005)
        assert ownership.process_identity(child.pid) is None
        with pytest.raises(PermissionError):os.killpg(child.pid,0)
        assert groups.remaining()==[child.pid]
        async def reap():
            cleanup=asyncio.create_task(groups.cleanup())
            await asyncio.sleep(.04)
            assert not cleanup.done()  # A zombie is not silently declared reaped.
            child.wait(timeout=2)
            await cleanup
        asyncio.run(reap())
        assert groups.remaining()==[]
    finally:
        child.stdin.close();child.wait(timeout=2);groups.close()


@pytest.mark.parametrize('deny_probe',[True,False])
@pytest.mark.parametrize('state_unavailable',[True,False])
def test_permission_denied_for_live_or_unknown_group_is_not_suppressed(monkeypatch,deny_probe,state_unavailable):
    import subprocess,sys
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    groups=ownership.ProcessGroups()
    try:
        groups.watch_root(child.pid)
        real_killpg=os.killpg
        def denied(pid,signum):
            if deny_probe or signum:raise PermissionError(errno.EPERM,'Operation not permitted')
            return real_killpg(pid,signum)
        if state_unavailable:
            def unavailable(*args,**kwargs):raise subprocess.TimeoutExpired('ps',.5)
            monkeypatch.setattr(ownership.subprocess,'run',unavailable)
        monkeypatch.setattr(ownership.os,'killpg',denied)
        if deny_probe:
            with pytest.raises(PermissionError):groups.remaining()
        with pytest.raises(PermissionError):groups.signal(signal.SIGTERM)
        assert child.poll() is None and child.pid in groups.groups
    finally:
        child.stdin.close();child.wait(timeout=2);groups.close()
