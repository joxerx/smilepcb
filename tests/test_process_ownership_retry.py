"""Exercise transient group-probe races without launching or signaling processes."""
import asyncio
import ctypes
import errno
import signal
from types import SimpleNamespace

import pytest
from smilepcb import process_ownership as ownership


@pytest.fixture
def registered(monkeypatch):
    groups=ownership.ProcessGroups()
    identity={'birth':[1,2],'pgid':42,'ppid':7}
    groups.groups[42]=identity
    clock={'now':0.0,'sleeps':[]}
    def sleep(delay):
        assert 0<delay<=.02
        clock['sleeps'].append(delay);clock['now']+=delay
    monkeypatch.setattr(ownership.time,'monotonic',lambda:clock['now'])
    monkeypatch.setattr(ownership.time,'sleep',sleep)
    monkeypatch.setattr(ownership,'process_identity',lambda pid:dict(identity))
    monkeypatch.setattr(ownership,'_darwin_zombie_only_group',lambda pid,**kwargs:False)
    try:yield groups,identity,clock
    finally:groups.close()


def denied():return PermissionError(errno.EPERM,'Operation not permitted')


def test_transient_permission_probe_rechecks_until_group_is_verified_gone(registered,monkeypatch):
    groups,identity,clock=registered;probes=[]
    def probe(pid,signum):
        assert (pid,signum)==(42,0);probes.append(pid)
        if len(probes)<=2:raise denied()
        raise ProcessLookupError(errno.ESRCH,'No such process')
    monkeypatch.setattr(ownership.os,'killpg',probe)
    assert groups.remaining()==[]
    assert not groups.groups and 0<clock['now']<=.1


def test_transient_signal_denial_does_not_claim_the_group_gone(registered,monkeypatch):
    groups,identity,clock=registered;signals=[]
    def probe(pid,signum):
        assert pid==42
        if not signum:return
        assert signum==signal.SIGTERM;signals.append(signum)
        if len(signals)<=2:raise denied()
    monkeypatch.setattr(ownership.os,'killpg',probe)
    groups.signal(signal.SIGTERM)
    assert groups.groups=={42:identity} and groups.remaining()==[42]
    assert signals==[signal.SIGTERM]*3 and 0<clock['now']<=.1


@pytest.mark.parametrize('identity_known',[False,True])
def test_persistent_permission_denial_keeps_group_registered_and_has_finite_budget(registered,monkeypatch,identity_known):
    groups,identity,clock=registered;probes=[]
    if not identity_known:monkeypatch.setattr(ownership,'process_identity',lambda pid:None)
    def probe(pid,signum):probes.append((pid,signum));raise denied()
    monkeypatch.setattr(ownership.os,'killpg',probe)
    with pytest.raises(PermissionError):groups.remaining()
    assert groups.groups=={42:identity}
    assert 2<len(probes)<=10 and 0<clock['now']<=.1


def test_pid_reuse_during_permission_retry_is_rejected_before_another_probe(registered,monkeypatch):
    groups,identity,clock=registered;identities=iter([identity,{**identity,'birth':[9,9]}]);probes=[]
    monkeypatch.setattr(ownership,'process_identity',lambda pid:next(identities))
    def probe(pid,signum):probes.append((pid,signum));raise denied()
    monkeypatch.setattr(ownership.os,'killpg',probe)
    with pytest.raises(RuntimeError,match='reused'):groups.remaining()
    assert probes==[(42,0)] and groups.groups=={42:identity}


def test_leader_exit_during_retry_does_not_discard_surviving_group(registered,monkeypatch):
    groups,identity,clock=registered;probes=[]
    monkeypatch.setattr(ownership,'process_identity',lambda pid:None)
    def probe(pid,signum):
        assert signum==0;probes.append(pid)
        if len(probes)<=2:raise denied()
    monkeypatch.setattr(ownership.os,'killpg',probe)
    assert groups.remaining()==[42] and groups.groups=={42:identity}


def test_cleanup_permission_retry_is_inside_existing_grace_budget(registered,monkeypatch):
    groups,identity,clock=registered
    def probe(pid,signum):raise denied()
    monkeypatch.setattr(ownership.os,'killpg',probe)
    with pytest.raises(PermissionError):asyncio.run(groups.cleanup(term_grace=.03,kill_grace=.03))
    assert groups.groups=={42:identity} and 0<clock['now']<=.03


def test_darwin_zombie_inspection_uses_remaining_probe_budget(monkeypatch):
    def members(kind,pgid,values,size):
        values[0]=42;return ctypes.sizeof(ctypes.c_int)
    monkeypatch.setattr(ownership.sys,'platform','darwin')
    monkeypatch.setattr(ownership,'_libproc',lambda:SimpleNamespace(proc_listpids=members))
    monkeypatch.setattr(ownership,'process_identity',lambda pid:None)
    monkeypatch.setattr(ownership.time,'monotonic',lambda:1.0)
    timeouts=[]
    def inspect(command,**kwargs):
        timeouts.append(kwargs['timeout']);return SimpleNamespace(stdout='42 42 Z\n')
    monkeypatch.setattr(ownership.subprocess,'run',inspect)
    assert ownership._darwin_zombie_only_group(42,deadline=1.03)
    assert len(timeouts)==1 and 0<timeouts[0]<=.030001


def test_cleanup_keeps_verified_zombie_registered_across_grace_phases(registered,monkeypatch):
    groups,identity,clock=registered
    def probe(pid,signum):
        if clock['now']<.06:raise denied()
        raise ProcessLookupError(errno.ESRCH,'No such process')
    async def yield_time(delay):clock['now']+=delay
    monkeypatch.setattr(ownership.os,'killpg',probe)
    monkeypatch.setattr(ownership.asyncio,'sleep',yield_time)
    monkeypatch.setattr(ownership,'_darwin_zombie_only_group',lambda pid,deadline:clock['now']<deadline)
    asyncio.run(groups.cleanup(term_grace=.03,kill_grace=.05))
    assert not groups.groups and .06<=clock['now']<=.08
