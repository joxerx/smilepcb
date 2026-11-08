"""Physical adjacency, real production exits and whole-profile domains."""
from copy import deepcopy
import time
import pytest
from smilepcb.keyboard_routing.coherent_via_banks import discover_cohorts,cohort_geometry,cap_cohort_domains
from smilepcb.keyboard_routing.bank_geometry import compatible_bank,port_outside_bounds


def pads():
    return [dict(ref='pkg',pad_id=str(i),net=net,point=(0,i*1000),faces=(2,),heading=(-1,0),
        bounds_nm=(-900,i*1000-100,1500,i*1000+100)) for i,net in enumerate(('a','b','fixed','c','d'))]


def test_actual_intervening_physical_pad_splits_selected_matrix_cohorts():
    cohorts,fallback=discover_cohorts(pads(),{'a','b','c','d'})
    assert {q['members'] for q in cohorts}=={('b','a'),('d','c')}
    assert not fallback
    assert all(len(q['pads'])==2 for q in cohorts)


def test_aliases_unsupported_faces_and_singletons_have_explicit_legacy_behavior():
    rows=pads();rows[1]['faces']=(0,2)
    cohorts,fallback=discover_cohorts(rows,{'a','b','c','d'})
    assert [q['members'] for q in cohorts]==[('d','c')]
    assert set(fallback)=={'a','b'}
    rows=pads();rows[1]['net']='a'
    cohorts,fallback=discover_cohorts(rows,{'a','c','d'})
    assert 'a' in fallback and all('a' not in q['members'] for q in cohorts)


def geometry(cohort,**kwargs):
    return cohort_geometry(cohort,body_face=0,width_nm=100,via_width_nm=300,via_drill_nm=200,
        clearance_nm=100,track_margin_nm=100,via_land_margin_nm=120,drill_land_margin_nm=80,
        runway_nm=500,exit_bounds_nm=(-4000,-2000,4000,8000),explicit_exit_bounds=True,
        maximum_distance_nm=12000,continuation_nm=700,deadline=time.monotonic()+2,**kwargs)


def test_real_exit_frame_and_runway_replace_short_synthetic_head_outlet():
    cohort=discover_cohorts(pads(),{'a','b'})[0][0]
    profiles=geometry(cohort)
    assert profiles
    for profile in profiles:
        for row in profile:
            assert port_outside_bounds(row['exit_nm'],(-1,0),(-4000,-2000,4000,8000),100)
            assert row['via_nm'][0]-row['exit_nm'][0]>=500
            assert row['future_end_nm']==(row['exit_nm'][0]-700,row['exit_nm'][1])
            assert row['heading_origin_nm']==row['via_nm']
            assert row['path'][-1]==row['via_nm']
        assert len({r['cohort_profile'] for r in profile})==1
    assert any(len({r['via_nm'][0] for r in q})>1 for q in profiles)


def test_rotation_preserves_asymmetric_land_geometry_and_profiles():
    rows=pads();cohort=discover_cohorts(rows,{'a','b'})[0][0]
    rotated=deepcopy(rows)
    for r in rotated:
        r['point']=(-r['point'][1],r['point'][0]);r['heading']=(0,-1)
        x0,y0,x1,y1=r['bounds_nm'];r['bounds_nm']=(-y1,x0,-y0,x1)
    other=discover_cohorts(rotated,{'a','b'})[0][0]
    assert other['members']==cohort['members']
    left,right=geometry(cohort),geometry(other)
    assert left and len(left)==len(right)
    for a,b in zip(left,right):
        for x,y in zip(a,b):
            assert y['via_nm']==(-x['via_nm'][1],x['via_nm'][0])
            assert y['path']==[(-p[1],p[0]) for p in x['path']]


def row(net,profile,cost=0):
    return dict(net=net,pad_ids=[net],kind='via',cohort='bank',cohort_pad_ids=('a','b'),cohort_profile=profile,cost=cost)


def test_via_selection_rejects_cross_family_and_unprofiled_member_mix():
    a=row('a','one');b=row('b','two')
    assert not compatible_bank(a,b)
    assert compatible_bank(a,row('b','one'))
    assert not compatible_bank(a,dict(net='b',pad_ids=['b'],kind='via'))
    assert compatible_bank(a,dict(net='other',pad_ids=['other'],kind='via'))


def test_caps_remove_incomplete_families_before_keeping_whole_profiles():
    domains={'a':[row('a','incomplete',-100),row('a','left',0),row('a','right',9)],
             'b':[row('b','left',9),row('b','right',0)]}
    kept,receipt=cap_cohort_domains(domains,('a','b'),{'a':1,'b':2},deadline=time.monotonic()+2)
    assert {q['cohort_profile'] for rows in kept.values() for q in rows}=={'left'}
    assert len(kept['a'])==len(kept['b'])==1
    assert receipt['available_profiles']==2 and receipt['retained_profiles']==1


def test_pair_native_rejection_discards_whole_profile_without_extra_budget():
    domains={n:[row(n,'bad'),row(n,'good',1)] for n in ('a','b')}
    kept,receipt=cap_cohort_domains(domains,('a','b'),{'a':1,'b':1},deadline=time.monotonic()+2,
        compatible=lambda a,b:a['cohort_profile']!='bad')
    assert all(rows[0]['cohort_profile']=='good' for rows in kept.values())
    assert receipt['peer_rejected_profiles']==1
    with pytest.raises(TimeoutError):cap_cohort_domains(domains,('a','b'),{'a':1,'b':1},deadline=0)


def test_existing_selector_rejects_cross_family_and_unprofiled_lane_values():
    import ast
    from pathlib import Path
    source=Path(__file__).parents[1]/'smilepcb/keyboard_routing/controller_ports.py'
    definition=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_select')
    namespace={'time':time}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),str(source),'exec'),namespace)
    domains=[[row('a','one'),row('a','two')],[row('b','two'),row('b','one')]]
    selected,stats=namespace['_select'](domains,compatible_bank,time.monotonic()+2,100)
    assert selected is not None
    assert len({domains[i][value]['cohort_profile'] for i,value in selected.items()})==1
    independent=dict(net='b',pad_ids=['b'],kind='via')
    rejected,_=namespace['_select']([[row('a','one')],[independent]],compatible_bank,time.monotonic()+2,100)
    assert rejected is None
    missing=row('a',None);assert not compatible_bank(missing,row('b',None))
    exhausted,stats=namespace['_select'](domains,compatible_bank,time.monotonic()+2,0)
    assert exhausted is None and stats['budget_exhausted'] and stats['states']==0
