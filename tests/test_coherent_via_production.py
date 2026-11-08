"""Exercise production source selection and forwarding with Native boundary doubles."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from test_source_budget_generator import scenario
from test_controller_escape_integration import collector_case
from smilepcb.keyboard_routing.bank_geometry import compatible_bank,port_outside_bounds


def coherent_scene(monkeypatch,**kwargs):
    import time
    monkeypatch.setattr(time,'monotonic',lambda:0.)
    case=scenario(aliases=('a',),second_net=True,fail_second=None)
    case.pads[1].point=(1000000,500000);case.footprint.GetOrientationDegrees=lambda:0
    case.scope['compatible_bank']=compatible_bank
    native_router=case.scope['LocalRouter']
    def router(*args,**kw):
        world=native_router(*args,**kw)
        world.generation=NS(foreign_copper_clearance_nm=0,via_to_smd_clearance_nm=0,via_to_hole_clearance_nm=251000)
        return world
    case.scope['LocalRouter']=router
    return case


def test_production_selector_uses_one_actual_cohort_profile_and_real_frame(monkeypatch):
    case=coherent_scene(monkeypatch)
    frame=(-2000000,-2000000,2000000,2000000)
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000,
        exit_bounds_nm={'MCU':frame},continuation_nm=500000,max_candidates=2)
    assert out['accepted'] and len(out['ports'])==2
    assert len({p['cohort_profile'] for p in out['ports']})==1
    assert all(p['kind']=='via' and port_outside_bounds(p['exit_nm'],(1,0),frame,150000) for p in out['ports'])
    assert all(p['profile']==p['cohort_profile'] for p in out['ports'])
    assert out['coherent_via_banks']['cohorts'][0]['retained_profiles']==2
    assert out['candidate_counts']==[2,2] and out['states']<=12000


def test_empty_coherent_menu_preserves_valid_legacy_source_selection(monkeypatch):
    case=coherent_scene(monkeypatch);native=case.scope['_coherent_via_candidate']
    def candidate(world,geometry,*args):
        return None if geometry['net']=='beta' else native(world,geometry,*args)
    case.scope['_coherent_via_candidate']=candidate
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000)
    assert out['accepted'] and len(out['ports'])==2
    receipt=out['coherent_via_banks']['cohorts'][0]
    assert receipt['retained_profiles']==0 and receipt['outcome']=='unchanged_no_compatible_profile'
    assert not out['coherent_via_banks']['complete_coherence']
    assert all('cohort' not in port for port in out['ports'])


def test_atomic_commit_rejection_rolls_back_all_cohort_lanes(monkeypatch):
    case=coherent_scene(monkeypatch);native=case.scope['LocalRouter']
    def router(*args,**kwargs):
        world=native(*args,**kwargs);add=world.add_path
        def broken(path,layer,net):
            add(path,layer,net)
            if net=='beta':raise TimeoutError('Controlled coherent commit rejection')
        world.add_path=broken;return world
    case.scope['LocalRouter']=router;before=case.snapshot()
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000)
    assert not out['accepted'] and 'commit rejection' in out['reason']
    assert case.snapshot()==before and not out['ports'] and not out['added_ids']


def test_opt_in_requires_boolean_and_explicit_selection_default_is_unchanged():
    implicit,explicit=scenario(),scenario()
    assert implicit.run()==explicit.run(coherent_via_banks=False)
    with pytest.raises(ValueError,match='boolean'):scenario().run(coherent_via_banks=1)
    with pytest.raises(ValueError,match='explicit selected'):scenario().run(coherent_via_banks=True)


def test_collector_forwards_opt_in_and_reports_honest_legacy_escape_fallback(monkeypatch):
    calls,result,deadline=collector_case(monkeypatch,coherent_via_banks=True)
    assert calls['stock'] and all(kwargs['coherent_via_banks'] is True for _,kwargs in calls['stock'])
    assert len(calls['fallback'])==1 and not result['candidate_accepted']
    first=result['trials'][0]
    provenance=first['escapes']['coherent_via_banks']
    assert provenance['outcome']=='legacy_escape_fallback' and not provenance['complete_coherence']
    assert provenance['failed_preference_receipt']==first['stock_escapes']
    assert calls['downstream'] and all(kwargs['deadline']<=deadline-12 for _,kwargs in calls['stock'])


def test_collector_default_forwarding_keeps_legacy_and_full_downstream_gate(monkeypatch):
    calls,result,deadline=collector_case(monkeypatch,stock_accepted=True,stock_elapsed=0)
    assert all(kwargs['coherent_via_banks'] is False for _,kwargs in calls['stock'])
    assert calls['downstream'] and not result['candidate_accepted']


@pytest.mark.parametrize('failure',[None,'via','land','runway','future','source'])
def test_native_adapter_preserves_every_existing_shape_guard_and_full_continuation(failure):
    import ast,math
    from pathlib import Path
    source=Path(__file__).parents[1]/'smilepcb/keyboard_routing/controller_ports.py'
    definition=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_coherent_via_candidate')
    calls=[];geometry=dict(net='a',path=[(0,0),(-1000,0)],layer=2,via_nm=(-1000,0),
        exit_nm=(-6000,0),exit_layer=0,future_end_nm=(-6500,0))
    def clear(shape,face,net):
        kind='future' if shape[1]==geometry['exit_nm'] else 'runway' if face==0 else 'source'
        calls.append((kind,shape));return failure!=kind
    world=NS(track_width=100,via_width=300,drill=200,via_clear=lambda *a:failure!='via',clear=clear)
    p=NS(F_Cu=0,B_Cu=2,SHAPE_SEGMENT=lambda a,b,w:('segment',a,b,w),SHAPE_CIRCLE=lambda a,r:('circle',a,r),
        SHAPE=NS(Collide=lambda *a:failure=='land'))
    from smilepcb.keyboard_routing.bank_geometry import clean_port_path
    namespace=dict(p=p,time=NS(monotonic=lambda:0),vec=tuple,distance=math.dist,clean_port_path=clean_port_path)
    exec(compile(ast.Module(body=[definition],type_ignores=[]),str(source),'exec'),namespace)
    diagnostic={}
    choice=namespace['_coherent_via_candidate'](world,geometry,['pad'],['land'],100,500,False,10,diagnostic)
    assert (choice is not None)==(failure is None)
    if choice is not None:
        assert ('future',('segment',(-6000,0),(-6500,0),100)) in calls
        assert ('runway',('segment',(-1000,0),(-6000,0),100)) in calls
        assert choice['circle']==('circle',(-1000,0),150) and choice['hole']==('circle',(-1000,0),100)
        assert len(choice['shapes'])==5 # source + both via faces + real runway + future
    else:assert diagnostic
    with pytest.raises(TimeoutError):namespace['_coherent_via_candidate'](world,geometry,['pad'],['land'],100,500,False,0,{})


def test_actual_cohort_domains_keep_shared_source_candidate_budget(monkeypatch):
    from smilepcb.keyboard_routing.conditional_sources import SourceSearchBudget
    case=coherent_scene(monkeypatch);budget=SourceSearchBudget([('a',),('b',)],max_candidates=1)
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000,
        max_candidates=8,source_budget=budget)
    assert out['accepted'] and out['candidate_counts']==[1,1]
    assert all(q['spent']==1 and q['remaining']==0 for q in out['source_budget']['candidate_domains'])
    assert out['coherent_via_banks']['cohorts'][0]['outcome']=='skipped_budget'
    assert all('cohort' not in port for port in out['ports'])


def test_physical_nonselected_pad_splits_source_domains_to_explicit_legacy(monkeypatch):
    from test_source_budget_generator import Pad
    case=coherent_scene(monkeypatch)
    fixed=Pad('intervening','fixed','physical',case.footprint);fixed.point=(1000000,250000)
    case.pads.append(fixed)
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000)
    assert out['accepted'] and out['coherent_via_banks']['cohorts']==[]
    assert set(out['coherent_via_banks']['legacy_nets'])=={'alpha','beta'}
    assert all('cohort' not in q for q in out['ports'])


def test_production_keeps_existing_coherent_same_layer_group(monkeypatch):
    from smilepcb.keyboard_routing.bank_geometry import escape_path,port_outside_bounds
    case=coherent_scene(monkeypatch)
    case.scope.update(escape_path=escape_path,port_outside_bounds=port_outside_bounds,
        _coherent_via_candidate=lambda *args:pytest.fail('Coherent same-layer group must not generate vias'))
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000,access_mode='prefer_same_layer')
    assert out['accepted'] and all(port['kind']=='same_layer' for port in out['ports'])
    receipt=out['coherent_via_banks']['cohorts'][0]
    assert receipt['outcome']=='kept_existing_coherent' and receipt['after']['via_count']==0
    assert receipt['additional_candidate_checks']=={'alpha':0,'beta':0}
    assert out['coherent_via_banks']['complete_coherence']


def test_refinement_exhaustion_preserves_the_original_commit_deadline(monkeypatch):
    from smilepcb.keyboard_routing import coherent_via_banks as api
    case=coherent_scene(monkeypatch);clock=NS(now=0.);clock.monotonic=lambda:clock.now
    case.scope['time']=clock;monkeypatch.setattr(api.time,'monotonic',clock.monotonic)
    seen=[];proof_deadlines=[];graph=case.scope['copper_graph']
    def checked_graph(board,nets,deadline):
        proof_deadlines.append(deadline);return graph(board,nets,deadline)
    case.scope['copper_graph']=checked_graph
    def exhausted(selected,cohorts,menu,**kwargs):
        seen.append(kwargs['deadline']);clock.now=kwargs['deadline']+.01
        return selected,[dict(outcome='skipped_deadline',after={'coherence':'mixed_or_unprofiled'})]
    monkeypatch.setattr(api,'refine_cohort_selection',exhausted)
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000)
    assert out['accepted'] and len(out['ports'])==2
    assert seen and seen[0]<10 and clock.now<10
    assert out['coherent_via_banks']['source_deadline']==10 and proof_deadlines==[10,10]


def test_late_feasible_legacy_selection_skips_refinement_for_commit_reserve(monkeypatch):
    from smilepcb.keyboard_routing import coherent_via_banks as api
    case=coherent_scene(monkeypatch);clock=NS(now=0.);clock.monotonic=lambda:clock.now
    case.scope['time']=clock;monkeypatch.setattr(api.time,'monotonic',clock.monotonic)
    select=case.scope['_select_access_domains']
    def late(*args,**kwargs):
        result=select(*args,**kwargs);clock.now=9.5;return result
    case.scope['_select_access_domains']=late
    monkeypatch.setattr(api,'refine_cohort_selection',lambda *a,**k:pytest.fail('Commit reserve is unavailable'))
    out=case.run(coherent_via_banks=True,include_nets={'alpha','beta'},max_distance_nm=2400000)
    assert out['accepted'] and out['coherent_via_banks']['outcome']=='skipped_commit_reserve'
    assert clock.now<10
