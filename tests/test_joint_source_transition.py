"""Pure group geometry and atomic ghost admission; no Native installation."""
from copy import deepcopy
import time
import pytest
from smilepcb.keyboard_routing.joint_source_transition import joint_source_proposals, admit_joint_sources


def lanes(source_pitch=1000,target_pitch=500,source_face=0,target_face=2):
    return [dict(net=str(i),source=(0,i*source_pitch),target=(12000,i*target_pitch),
                 source_layer=source_face,target_layer=target_face,heading=(1,0),width=100) for i in range(3)]


def proposals(rows,**kwargs):
    return joint_source_proposals(rows,via_width_nm=300,clearance_nm=100,deadline=time.monotonic()+2,**kwargs)


def test_ordered_pitch_conversion_and_identity():
    for rows in (lanes(),lanes(source_pitch=500),lanes(source_pitch=1400,target_pitch=700)):
        candidates=proposals(rows)
        assert candidates and len(candidates)<=64
        for candidate in candidates:
            for row,route in zip(rows,candidate['routes']):
                assert route['legs'][0][1][0]==row['source']
                assert route['legs'][-1][1][-1]==row['target']
                for phase,(face,path) in enumerate(route['legs']):
                    side=-1 if phase==0 and route['vias'] and candidate['escape_side']=='inward' else 1
                    assert all(side*(b[0]-a[0])>0 and (b[1]==a[1] or abs(b[1]-a[1])==abs(b[0]-a[0])) for a,b in zip(path,path[1:]))


@pytest.mark.parametrize('transform,heading',[(lambda p:(-p[0],p[1]),(-1,0)),(lambda p:(-p[1],p[0]),(0,1))])
def test_mirrored_and_rotated_geometry(transform,heading):
    original=lanes();changed=deepcopy(original)
    for row in changed:
        row.update(source=transform(row['source']),target=transform(row['target']),heading=heading)
    answer=proposals(changed)
    assert answer
    for row,route in zip(changed,answer[0]['routes']):
        assert route['legs'][0][1][0]==row['source'] and route['legs'][-1][1][-1]==row['target']


def test_coordinated_vias_are_on_wide_source_side_before_compression():
    rows=lanes();answer=proposals(rows)
    assert any(len({route['vias'][0][0] for route in q['routes']})>1 for q in answer)
    for candidate in answer:
        for row,route in zip(rows,candidate['routes']):
            assert len(route['vias'])==1
            assert route['vias'][0][1]-row['source'][1]==candidate['source_shift_nm']
            assert route['legs'][0][0]==0 and route['legs'][1][0]==2


def test_same_face_does_not_add_vias():
    assert all(not route['vias'] and len(route['legs'])==1 for q in proposals(lanes(target_face=0)) for route in q['routes'])


@pytest.mark.parametrize('bad',['permutation','heading','mixed-heading','mixed-width','nonrail','backwards'])
def test_unsupported_group_is_explicitly_rejected(bad):
    rows=lanes()
    if bad=='permutation':rows[0]['target'],rows[1]['target']=rows[1]['target'],rows[0]['target']
    if bad=='heading':rows[0]['heading']=(1,1)
    if bad=='mixed-heading':rows[1]['heading']=(0,1)
    if bad=='mixed-width':rows[1]['width']=101
    if bad=='nonrail':rows[1]['target']=(12001,500)
    if bad=='backwards':rows[1]['target']=(-1,500)
    with pytest.raises(ValueError):proposals(rows)


def test_limit_and_deadline_are_enforced():
    assert len(proposals(lanes(),limit=2))<=2
    assert joint_source_proposals(lanes(),via_width_nm=300,clearance_nm=100,deadline=0)==[]
    with pytest.raises(ValueError):proposals(lanes(),limit=65)


class World:
    def __init__(self):self.operations=[{'body':True}];self.track_width=100
    def checkpoint(self):return deepcopy(self.operations)
    def restore(self,mark):self.operations=deepcopy(mark)


def test_one_bad_lane_restores_entire_proposal_and_records_failure():
    world=World();seen=[]
    def check(world,route,row):
        seen.append(row['net']);world.operations.append({'net':row['net']})
        return (row['net']!='1',{'reason':'blocked','collisions':['fixed-copper']})
    result=admit_joint_sources(world,lanes(),via_width_nm=300,clearance_nm=100,deadline=time.monotonic()+2,checker=check,limit=2)
    assert not result['accepted'] and world.operations==[{'body':True}]
    assert seen==['0','1','0','1']
    assert result['attempts'][0]['net']=='1'
    assert result['attempts'][0]['evidence']['collisions']==['fixed-copper']


def test_success_exports_per_net_ordered_operations_and_keeps_all_lanes():
    world=World()
    def check(world,route,row):
        world.operations.append({'net':row['net'],'legs':route['legs']});return True,{}
    result=admit_joint_sources(world,lanes(),via_width_nm=300,clearance_nm=100,deadline=time.monotonic()+2,checker=check)
    assert result['accepted'] and set(result['operations'])=={'0','1','2'}
    assert len(world.operations)==4 and result['accepted_strategy']=='joint'


def test_checker_exception_and_expired_admission_roll_back():
    world=World()
    def check(world,route,row):
        world.operations.append({'net':row['net']});raise RuntimeError('native exception')
    with pytest.raises(RuntimeError):admit_joint_sources(world,lanes(),via_width_nm=300,clearance_nm=100,deadline=time.monotonic()+2,checker=check)
    assert world.operations==[{'body':True}]


def test_wide_side_jog_is_coherent_and_precedes_pitch_compression():
    answer=proposals(lanes())
    assert {q['source_shift_nm']>0 for q in answer}=={False,True}
    assert any(q['source_shift_nm']<0 for q in answer)
    assert len(answer)<=64
    for candidate in answer:
        if not candidate['source_shift_nm']:continue
        for row,route in zip(lanes(),candidate['routes']):
            first=route['legs'][0][1]
            assert first[1][1]==row['source'][1] # cardinal physical launch
            assert first[-1]==route['vias'][0]
            assert first[-1][1]-first[0][1]==candidate['source_shift_nm']


def test_normal_pitch_schedule_keeps_compact_diagonals_physically_separated():
    from smilepcb.keyboard_routing.bundle_geometry import paths_clear
    rows=lanes(source_pitch=500,target_pitch=326,target_face=0)
    for row in rows:row['width']=150
    rows[0]['target']=(12000,-1000);rows[1]['target']=(12000,-674);rows[2]['target']=(12000,-348)
    candidates=joint_source_proposals(rows,via_width_nm=500,clearance_nm=151,deadline=time.monotonic()+2)
    assert any(paths_clear([r['legs'][0][1] for r in q['routes']],[150]*3,151) for q in candidates)


def test_elongated_nonuniform_lands_set_each_via_and_first_bend_distance():
    rows=lanes()
    for i,row in enumerate(rows):
        row.update(source_forward_extent_nm=900+i*200,source_backward_extent_nm=400+i*300,
                   source_drill_land_margin_nm=80,source_via_copper_margin_nm=120)
    answer=joint_source_proposals(rows,via_width_nm=300,via_drill_nm=200,clearance_nm=100,deadline=time.monotonic()+2)
    assert answer
    for q in answer:
        side=1 if q['escape_side']=='outward' else -1
        extent_key='source_forward_extent_nm' if side==1 else 'source_backward_extent_nm'
        for row,route in zip(rows,q['routes']):
            via=route['vias'][0]
            assert side*(via[0]-row['source'][0])>=row[extent_key]+max(150+120,100+80)
            first=route['legs'][0][1]
            if len(first)>2:
                assert side*(first[1][0]-row['source'][0])>=row[extent_key]+50+100


def test_inward_comb_returns_on_other_face_without_same_face_reversal():
    from smilepcb.keyboard_routing.layered_simple_route import safe_layered_route
    answer=proposals(lanes())
    assert [q['escape_side'] for q in answer[:2]]==['outward','inward']
    for q in answer:
        for row,route in zip(lanes(),q['routes']):
            first,last=route['legs']
            side=1 if q['escape_side']=='outward' else -1
            assert all(side*(b[0]-a[0])>0 for a,b in zip(first[1],first[1][1:]))
            assert all(b[0]>a[0] for a,b in zip(last[1],last[1][1:]))
            assert safe_layered_route(route,row['width'],300)
    assert all(q['escape_side']=='outward' for q in proposals(lanes(target_face=0)))
    assert len(answer)<=64


def test_elongated_land_margins_rotate_with_physical_heading():
    original=lanes()
    for row in original:row.update(source_forward_extent_nm=900,source_backward_extent_nm=1500)
    rotated=deepcopy(original)
    for row in rotated:row.update(source=(-row['source'][1],row['source'][0]),target=(-row['target'][1],row['target'][0]),heading=(0,1))
    a,b=proposals(original),proposals(rotated)
    assert a and len(a)==len(b)
    for qa,qb in zip(a,b):
        assert qa['escape_side']==qb['escape_side']
        for ra,rb in zip(qa['routes'],qb['routes']):
            assert rb['vias']==[(-q[1],q[0]) for q in ra['vias']]


def test_regression_source_via_cannot_be_inside_elongated_source_land():
    rows=lanes()
    for row in rows:row.update(source_forward_extent_nm=900,source_backward_extent_nm=1500)
    answer=proposals(rows)
    assert answer
    for q in answer:
        for row,route in zip(rows,q['routes']):
            assert abs(route['vias'][0][0]-row['source'][0])>900+150


def test_native_land_metadata_uses_actual_asymmetric_bbox_and_heading():
    import ast
    from pathlib import Path
    from types import SimpleNamespace as NS
    source=Path(__file__).parents[1]/'smilepcb/keyboard_routing/obstacle_bundles.py'
    tree=ast.parse(source.read_text())
    definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_source_land_metadata')
    namespace={'p':NS(FromMM=lambda x:round(x*1000000),VECTOR2I=lambda *q:q,SHAPE_CIRCLE=lambda q,r:q,SHAPE=NS(Collide=lambda *a:True))}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),str(source),'exec'),namespace)
    box=NS(GetLeft=lambda:-900,GetRight=lambda:1500,GetTop=lambda:-400,GetBottom=lambda:700)
    pad=NS(GetNumber=lambda:'terminal',GetNetname=lambda:'signal',IsOnLayer=lambda f:f==2,GetEffectiveShape=lambda f:NS(BBox=lambda:box),m_Uuid=NS(AsString=lambda:'pad-id'))
    board=NS(GetFootprints=lambda:[NS(GetReference=lambda:'package',Pads=lambda:[pad])])
    world=NS(board=board,generation=NS(via_to_smd_clearance_nm=230),net_clearances={'signal':100},minimum_clearance=90)
    row=dict(net='signal',mcu_ref='package',mcu_pad='terminal',heading=(1,0),window=dict(source=(0,0),source_layer=2))
    for heading,extents in [((1,0),(1500,900)),((-1,0),(900,1500)),((0,1),(700,400)),((0,-1),(400,700))]:
        row['heading']=heading;metadata=namespace['_source_land_metadata'](world,row)
        assert (metadata['source_forward_extent_nm'],metadata['source_backward_extent_nm'])==extents
        assert metadata['source_drill_land_margin_nm']==100000 and metadata['source_via_copper_margin_nm']==230
    row['mcu_pad']='missing'
    with pytest.raises(ValueError,match='source pad'):namespace['_source_land_metadata'](world,row)


def test_pad_identity_cannot_silently_default_missing_land_extents():
    rows=lanes();rows[0]['source_pad_id']='physical-pad'
    with pytest.raises(ValueError,match='land extents'):proposals(rows)


def test_same_face_first_bend_clears_actual_land_and_track_margin():
    rows=lanes(target_face=0)
    for row in rows:row.update(source_forward_extent_nm=1900,source_backward_extent_nm=500,source_track_margin_nm=230)
    for q in proposals(rows):
        for row,route in zip(rows,q['routes']):
            path=route['legs'][0][1]
            if len(path)>2:assert path[1][0]-row['source'][0]>=1900+50+230


def test_inward_escape_side_is_retained_in_success_receipt():
    world=World()
    def check(world,route,row):
        inward=route['vias'][0][0]<row['source'][0]
        if inward:world.operations.append({'net':row['net']})
        return inward,{'reason':'outward-blocked'}
    result=admit_joint_sources(world,lanes(),via_width_nm=300,clearance_nm=100,deadline=time.monotonic()+2,checker=check,limit=2)
    assert result['accepted'] and result['escape_side']=='inward'
    assert [a['escape_side'] for a in result['attempts']]==['outward','inward']


def test_actual_drill_land_margin_can_dominate_annulus_margin():
    rows=lanes()
    for row in rows:row.update(source_forward_extent_nm=900,source_backward_extent_nm=1500,source_drill_land_margin_nm=700)
    answer=joint_source_proposals(rows,via_width_nm=300,via_drill_nm=200,clearance_nm=100,deadline=time.monotonic()+2)
    assert answer
    for q in answer:
        side=1 if q['escape_side']=='outward' else -1
        extent=900 if side==1 else 1500
        for row,route in zip(rows,q['routes']):assert side*(route['vias'][0][0]-row['source'][0])>extent+100+700


def owned_prefix_metadata_fixture():
    import ast
    from pathlib import Path
    from types import SimpleNamespace as NS
    from test_source_prefixes import fixture,variant,FAKE_P
    board,row=fixture();owned=variant(board,row)
    source=Path(__file__).parents[1]/'smilepcb/keyboard_routing/obstacle_bundles.py'
    definitions=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in ('_source_land_metadata','_owned_prefix_terminal')]
    namespace={'__package__':'smilepcb.keyboard_routing','p':NS(**vars(FAKE_P),FromMM=lambda x:round(x*1000000))}
    exec(compile(ast.Module(body=definitions,type_ignores=[]),str(source),'exec'),namespace)
    world=NS(board=board,generation=NS(via_to_smd_clearance_nm=230),net_clearances={'signal':100},minimum_clearance=90)
    return namespace['_source_land_metadata'],world,owned


def test_certified_opposite_face_owned_prefix_uses_actual_via_context():
    helper,world,row=owned_prefix_metadata_fixture()
    metadata=helper(world,row)
    assert metadata['source_terminal_kind']=='owned-prefix-via'
    assert metadata['source_terminal_id']==row['source_prefix_witness']['via']['id']
    assert metadata['source_forward_extent_nm']==metadata['source_backward_extent_nm']==310000
    assert metadata['source_terminal_drill_nm']==300000
    assert 'source_pad_id' not in metadata


@pytest.mark.parametrize('change',['moved-source','moved-via','wrong-net','changed-drill','missing-owner','changed-prefix','missing-witness'])
def test_owned_prefix_context_rejects_stale_or_uncertified_terminals(change):
    helper,world,row=owned_prefix_metadata_fixture()
    if change=='moved-source':row['window']['source']=(2_000_000,1_000_000)
    if change=='moved-via':world.board.items[2].a=(2_000_000,1_000_000)
    if change=='wrong-net':world.board.items[2].net='foreign'
    if change=='changed-drill':world.board.items[2].drill+=1
    if change=='missing-owner':row['ids'].remove('via')
    if change=='changed-prefix':world.board.items[1].a=(600000,0)
    if change=='missing-witness':row.pop('source_prefix_witness')
    with pytest.raises(ValueError):helper(world,row)


def test_uncertified_moved_source_on_pad_face_is_rejected():
    helper,world,row=owned_prefix_metadata_fixture();row.pop('source_prefix_witness')
    row['window'].update(source=(10_000_000,10_000_000),source_layer=2)
    with pytest.raises(ValueError,match='does not contact'):helper(world,row)


def test_joint_admission_accepts_certified_owned_prefix_terminal_metadata():
    helper,native_world,row=owned_prefix_metadata_fixture();metadata=helper(native_world,row)
    lane=dict(net=row['net'],source=row['window']['source'],source_layer=row['window']['source_layer'],
        target=(-8_000_000,1_000_000),target_layer=0,heading=row['heading'],width=row['width'],**metadata)
    world=World()
    def check(world,route,lane):
        assert lane['source_terminal_id']=='via' and not route['vias']
        world.operations.append({'net':lane['net']});return True,{}
    result=admit_joint_sources(world,[lane],via_width_nm=500000,via_drill_nm=200000,clearance_nm=151000,
        deadline=time.monotonic()+2,checker=check)
    assert result['accepted'] and result['accepted_strategy']=='joint'
    assert result['source_lands'][0]['source_terminal_kind']=='owned-prefix-via'
