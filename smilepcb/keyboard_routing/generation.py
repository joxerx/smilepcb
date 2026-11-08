"""Explicit, request-scoped margins for newly generated native copper.

These policies never change project rules or the representation of source
copper. A router captures its policy when constructed; workers select a fresh
policy for each request, including requests which omit the profile field.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class GenerationProfile:
    name: str
    foreign_copper_clearance_nm: int = 0
    edge_clearance_nm: int = 0
    via_to_smd_clearance_nm: int = 0
    via_to_hole_clearance_nm: int = 251000


_PROFILES = {
    'native': GenerationProfile('native'),
    'dsn-safe-v1': GenerationProfile('dsn-safe-v1', 173000, 251000, 350000, 401000),
    # Experimental creation policy; native/exchange qualification is separate.
    'dsn-process-v2': GenerationProfile('dsn-process-v2', 173000, 251000, 173000, 251000),
}
_current = ContextVar('smilepcb_generation_profile', default=_PROFILES['native'])


def current_generation_profile():
    return _current.get()


def select_generation_profile(name='native'):
    """Select an explicit worker policy, rejecting null and non-string input."""
    if type(name) is not str or name not in _PROFILES:
        raise ValueError('Unknown generation profile; expected native, dsn-safe-v1 or dsn-process-v2')
    profile = _PROFILES[name]
    _current.set(profile)
    return profile


@contextmanager
def generation_profile(name='native'):
    """Scope a request policy and restore the caller even when it fails."""
    previous = _current.get()
    profile = select_generation_profile(name)
    try:
        yield profile
    finally:
        _current.set(previous)


def uses_exchange_geometry(profile):
    """Both named exchange policies retain octile paths and NPTH enclosure."""
    return getattr(profile,'name',profile) in ('dsn-safe-v1','dsn-process-v2')


def bind_generation_profile(profile,*,native_copper_clearance_nm,native_hole_clearance_nm):
    """Resolve only experimental via creation gaps from actual native floors."""
    if profile.name!='dsn-process-v2':return profile
    if (type(native_copper_clearance_nm) is not int or native_copper_clearance_nm<0 or
        type(native_hole_clearance_nm) is not int or native_hole_clearance_nm<=0):
        raise ValueError('Expected integer native copper/hole clearance floors')
    return replace(profile,
        via_to_smd_clearance_nm=max(native_copper_clearance_nm,profile.foreign_copper_clearance_nm),
        via_to_hole_clearance_nm=native_hole_clearance_nm+1000)
