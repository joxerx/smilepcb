"""Host-only process binding and exchange geometry; no native backend."""
from dataclasses import asdict
import pytest
from smilepcb.keyboard_routing import generation as policies


def bind(name,copper,hole):
    with policies.generation_profile(name) as profile:
        binder=getattr(policies,'bind_generation_profile',None)
        assert binder is not None,'Creation margins must bind to actual native floors'
        return binder(profile,native_copper_clearance_nm=copper,native_hole_clearance_nm=hole)


@pytest.mark.parametrize('copper,hole,want',[(150000,250000,(173000,251000)),(300000,400000,(300000,401000)),(0,250000,(173000,251000))])
def test_v2_binds_maximum_copper_and_actual_native_hole_floor(copper,hole,want):
    result=bind('dsn-process-v2',copper,hole)
    assert (result.via_to_smd_clearance_nm,result.via_to_hole_clearance_nm)==want
    assert (result.foreign_copper_clearance_nm,result.edge_clearance_nm)==(173000,251000)


@pytest.mark.parametrize('name,want',[('native',(0,251000)),('dsn-safe-v1',(350000,401000))])
def test_binding_v2_cannot_change_existing_native_or_v1_policy(name,want):
    result=bind(name,900000,1200000)
    assert (result.via_to_smd_clearance_nm,result.via_to_hole_clearance_nm)==want
    with policies.generation_profile(name) as original:assert asdict(result)==asdict(original)
    assert policies.current_generation_profile().name=='native'


@pytest.mark.parametrize('copper,hole',[(True,250000),(-1,250000),(150000,False),(150000,0),(150000,250000.0)])
def test_v2_binding_rejects_invalid_native_dimensions(copper,hole):
    with pytest.raises(ValueError):bind('dsn-process-v2',copper,hole)


@pytest.mark.parametrize('name,expected',[('native',False),('dsn-safe-v1',True),('dsn-process-v2',True)])
def test_both_exchange_policies_require_the_same_exchange_geometry(name,expected):
    helper=getattr(policies,'uses_exchange_geometry',None)
    assert helper is not None,'Octile and NPTH consumers need one shared capability'
    with policies.generation_profile(name) as profile:assert helper(profile) is expected


def test_v2_context_restores_v1_even_after_failure():
    with policies.generation_profile('dsn-safe-v1'):
        with pytest.raises(RuntimeError):
            with policies.generation_profile('dsn-process-v2'):raise RuntimeError('candidate rejected')
        assert policies.current_generation_profile().name=='dsn-safe-v1'
    assert policies.current_generation_profile().name=='native'


@pytest.mark.parametrize('requested,upstream,want',[(None,None,'native'),(None,'dsn-safe-v1','native'),('native','dsn-safe-v1','native'),('dsn-safe-v1','dsn-safe-v1','dsn-safe-v1'),('dsn-process-v2','dsn-process-v2','dsn-process-v2')])
def test_completion_keeps_legacy_native_and_requires_explicit_matching_v2(requested,upstream,want):
    from smilepcb.stm32 import context
    resolver=getattr(context,'resolve_completion_profile',None)
    assert resolver is not None,'Completion must resolve actual stage provenance explicitly'
    assert resolver(requested,upstream)==want


@pytest.mark.parametrize('requested,upstream',[('dsn-process-v2',None),('dsn-process-v2','dsn-safe-v1'),(None,'dsn-process-v2'),('native','dsn-process-v2'),('unknown','dsn-safe-v1')])
def test_completion_rejects_inconsistent_new_profile_provenance(requested,upstream):
    from smilepcb.stm32 import context
    resolver=getattr(context,'resolve_completion_profile',None)
    assert resolver is not None,'Completion must reject profile mismatches'
    with pytest.raises(ValueError):resolver(requested,upstream)
