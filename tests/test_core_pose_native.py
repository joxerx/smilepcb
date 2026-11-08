"""Host contracts for the isolated Native adapter; no Native qualification."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from smilepcb.stm32 import core_pose_native as native


def test_raw_assembly_input_rejects_already_imported_core_model():
    with pytest.raises(ValueError,match='raw'):
        native.validate_raw_model({'parts':[{'ref':'U_MCU'},{'ref':'C_CORE1'}]})
    native.validate_raw_model({'parts':[{'ref':'U_MCU'},{'ref':'C_VDD1'}]})


def test_exact_pose_does_not_fall_back_and_controls_default_off():
    assert native.assembly_controls({})==dict(exact_pose=None,assembly_only=False,fixed_pose=None)
    with pytest.raises(ValueError):native.assembly_controls({'_core_exact_pose':{'origin_nm':[1,2],'rotation_deg':45}})
    pose={'origin_nm':[1,2],'rotation_deg':90}
    assert native.assembly_controls({'_core_exact_pose':pose,'_core_assembly_only':True})['exact_pose']==pose


def test_resume_uses_selected_saved_scene_and_preserves_root_manifest(monkeypatch):
    calls=[];scene={'pose':{'origin_nm':[1,2],'rotation_deg':0},'expected_source_keys':[['U_MCU:1'],['U_MCU:2']],
        'native_source_manifest':{'mandatory_pad_keys':['U_MCU:1','U_MCU:2'],'pads':[{'pad_key':'U_MCU:1','pad_id':'a'},{'pad_key':'U_MCU:2','pad_id':'b'}],
            'roots':[{'source_key':['U_MCU:1'],'pad_ids':['a']},{'source_key':['U_MCU:2'],'pad_ids':['b']}]}}
    class Runtime:
        def __init__(self,*a,**k):pass
        def poses(self):return [scene['pose']]
        def assemble(self,*a,**k):calls.append('assemble');return scene
        def evaluate(self,*a,**k):calls.append('evaluate');return {}
        def dispose(self,*a):calls.append('dispose')
        def resume(self,selected,result):assert selected is scene;calls.append('resume');return {'accepted':True,'scene':selected}
        def verify_baseline(self):calls.append('baseline_exact')
    monkeypatch.setattr(native,'_runtime',Runtime)
    def select(poses,**kw):
        actual=kw['assemble'](poses[0],deadline=10);kw['evaluate'](actual,deadline=10)
        return dict(selected_scene=actual,selected_pose=poses[0],improved=False,attempts=[],reason='baseline_retained')
    monkeypatch.setattr(native,'_select',select)
    result=native.lookahead_core_pose(object(),{}, {},core_objects=[],baseline_pose=scene['pose'],fixed_pose={},deadline=10)
    assert result['scene'] is scene and calls==['assemble','evaluate','resume','baseline_exact']


def test_fixed_usb_pose_cannot_search_another_connector_location(monkeypatch):
    import ast
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'smilepcb/stm32/usb_placement.py'
    node=next(q for q in ast.parse(path.read_text()).body if isinstance(q,ast.FunctionDef) and q.name=='select_usb_placement')
    pose={'origin_nm':[1,2],'rotation_deg':90.,'side':'B.Cu'}
    class Connector:
        m_Uuid=NS(AsString=lambda:'connector')
        def SetOrientationDegrees(self,a):assert a==90
        def SetPosition(self,p):assert p==(1,2)
    connector=Connector();state={'tracks':{},'footprints':{}};calls=[]
    board=NS(GetTracks=lambda:[],BuildConnectivity=lambda:None)
    def route(*a,**k):calls.append(1);return {'accepted':False,'reason':'fixed failed'}
    ns=dict(time=NS(monotonic=lambda:0),snapshot=lambda b:deepcopy(state),_pose=lambda *a:deepcopy(pose),
        _fingerprint=lambda *a:'hash',_candidates=lambda *a:pytest.fail('Fixed connector cannot search'),p=NS(VECTOR2I=lambda *p:p),route_controller_usb=route)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    result=ns['select_usb_placement'](board,connector,{'parts':[{'ref':'J_UDB'}]},preferred_nm=[1,2],legal=lambda f:True,deadline=10,fixed_pose=pose)
    assert not result['accepted'] and len(calls)==1


def test_rejected_geometric_assembly_is_saved_but_cannot_enter_local_proof(monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    fail=lambda *a,**k:pytest.fail('Rejected geometry cannot enter admission')
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(snapshot=lambda b:{}))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_local',NS(prepare_controller_local=fail))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.power',NS(seed_controller_returns=fail))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_plan',NS(_local_subsets=fail,verify_controller_local_subsets=fail))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=fail))
    monkeypatch.setitem(sys.modules,'smilepcb.stm32.usb_placement',NS(select_usb_placement=fail,verify_prepared_usb=fail))
    monkeypatch.setattr(native,'_mechanics',lambda *a:{})
    monkeypatch.setattr(native,'_rules',lambda *a:{})
    runtime=native._Runtime.__new__(native._Runtime);runtime.mechanics={};runtime.context={}
    scene=dict(pose={},expected_source_keys=[['U_MCU:1']],resource=dict(board=object(),cfg={'parts':[]},assembly={'assembly_checks':{'exact_core_legal':False,'all_auxiliary':True,'final_leds':True}}))
    proof=runtime.evaluate(scene,deadline=10)
    assert proof['checks']['assembly'] is False and proof['proved_source_keys']==[]


def test_rejected_scene_disposal_preserves_actual_cad_for_comparison(tmp_path):
    saved=tmp_path/'assembly.kicad_pcb';saved.write_text('actual isolated saved CAD')
    scene={'resource':{'directory':tmp_path,'board':object(),'owners':[object()]}}
    native._Runtime.__new__(native._Runtime).dispose(scene)
    assert saved.read_text()=='actual isolated saved CAD' and scene['resource']['disposed']
    assert 'board' not in scene['resource'] and 'owners' not in scene['resource']


def test_mechanics_duplicate_number_pads_are_canonical_but_movement_is_detected(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(snapshot=lambda board:deepcopy(board)))
    pads=[dict(number='',position=[10,20],size=[3,3],uuid='a',net=''),
          dict(number='',position=[30,20],size=[3,3],uuid='b',net='')]
    before=dict(footprints={'old':dict(ref='SW1',position=[20,20],rotation=0,pads=pads)})
    after=deepcopy(before);after['footprints']['old']['pads'].reverse()
    for i,pad in enumerate(after['footprints']['old']['pads']):pad['uuid']='new-'+str(i)
    cfg={'parts':[{'ref':'SW1','region':'matrix'}]}
    assert native._mechanics(before,cfg)==native._mechanics(after,cfg)
    after['footprints']['old']['pads'][0]['position'][0]+=1
    assert native._mechanics(before,cfg)!=native._mechanics(after,cfg)


def test_finite_pose_events_share_cap_across_rotations_and_pitch_diverse_positions():
    baseline=dict(origin_nm=[0,0],rotation_deg=180,pose_key='baseline')
    events=[(100*n+epsilon,0) for n in range(1,30) for epsilon in (0,1)]
    poses=native._bounded_pose_events(baseline,events,pitch_nm=800,limit=48)
    assert poses[0]==baseline and len(poses)==48
    assert {angle:sum(p['rotation_deg']==angle for p in poses) for angle in (0,90,180,270)}=={0:12,90:12,180:12,270:12}
    points={tuple(p['origin_nm']) for p in poses}
    assert all(max(abs(a[i]-b[i]) for i in (0,1))>=100 for a in points for b in points if a!=b)
    assert all(tuple(p['origin_nm'])==(0,0) or tuple(p['origin_nm']) in events for p in poses)


def test_whole_core_side_space_is_connected_conservative_and_has_pad_hole_witness():
    core=[dict(box=[40,40,60,60],layer=2,kind='body')]
    fixed=[dict(box=[15,35,25,65],layer=0,kind='hole',ref='SW57',pad=''),
           dict(box=[70,0,80,20],layer=2,kind='pad',ref='REMOTE',pad='1')]
    result=native._space_metrics(core,fixed,[0,0,100,100],access_depth_nm=5)
    assert result['legal'] is True
    assert result['side_space_nm']==dict(left=15,right=40,top=40,bottom=40)
    assert result['side_witnesses']['left']['ref']=='SW57'
    assert result['side_witnesses']['left']['kind']=='hole'
    assert result['bank_headroom_nm']==10
    # A free remote interval cannot conceal a blockage in the connected side slab.
    fixed.append(dict(box=[61,45,65,55],layer=2,kind='body',ref='BLOCK',pad='1'))
    assert native._space_metrics(core,fixed,[0,0,100,100],access_depth_nm=5)['bank_headroom_nm']==-4


def test_whole_core_metrics_use_caps_and_copper_and_reject_fixed_pad_collision():
    core=[dict(box=[40,40,60,60],layer=2,kind='body'),dict(box=[25,45,35,55],layer=2,kind='copper')]
    obstacle=dict(box=[10,40,20,60],layer=2,kind='pad',ref='SW1',pad='1',gap_nm=2)
    result=native._space_metrics(core,[obstacle],[0,0,100,100],access_depth_nm=5)
    assert result['whole_core_bounds_nm']==[25,40,60,60] and result['side_space_nm']['left']==3
    moved=native._transform_features(core,[0,0],0,[5,0],0)
    assert native._space_metrics(moved,[obstacle],[0,0,100,100],access_depth_nm=5)['side_space_nm']['left']==8
    obstacle['box']=[30,45,33,55]
    assert native._space_metrics(core,[obstacle],[0,0,100,100],access_depth_nm=5)['legal'] is False


def test_opposite_face_smd_cannot_flatten_whole_core_side_metrics():
    core=[dict(box=[40,40,60,60],layer=2,kind='pad')]
    opposite=[dict(box=[45,45,55,55],layer=0,kind='pad',ref='FRONT',pad='1')]
    result=native._space_metrics(core,opposite,[0,0,100,100],access_depth_nm=5)
    assert result['legal'] is True and result['side_space_nm']==dict(left=40,right=40,top=40,bottom=40)


def test_bank_halo_is_bounded_one_depth_and_does_not_reserve_all_free_space():
    pads=[dict(box=[10,10,12,14],heading=[1,0]),dict(box=[10,20,12,24],heading=[1,0]),dict(box=[0,10,2,14],heading=[-1,0])]
    halos=native._bank_access_halos(pads,depth_nm=5,margin_nm=2)
    assert {tuple(h['heading']):h['box'] for h in halos}=={(-1,0):[-5,8,0,16],(1,0):[12,8,17,26]}
    assert all(h['depth_nm']==5 for h in halos)


def test_side_and_collision_gaps_preserve_larger_owned_core_requirement():
    core=[dict(box=[40,40,60,60],layer=2,kind='copper',gap_nm=7)]
    fixed=[dict(box=[20,40,30,60],layer=2,kind='pad',gap_nm=2,ref='SW1',pad='1')]
    result=native._space_metrics(core,fixed,[0,0,100,100],access_depth_nm=5)
    assert result['legal'] is True and result['side_space_nm']['left']==3
    fixed[0]['box']=[20,40,34,60]
    assert native._space_metrics(core,fixed,[0,0,100,100],access_depth_nm=5)['legal'] is False


def test_bucketed_oracle_preserves_full_inventory_result_without_native_collision():
    core=[dict(box=[40,40,60,60],layer=2,kind='body')]
    fixed=[dict(box=[15,35,25,65],layer=2,kind='hole',ref='SW57',pad=''),dict(box=[80,80,90,90],layer=2,kind='pad')]
    oracle=native._NativePoseOracle.__new__(native._NativePoseOracle)
    oracle.core=core;oracle.fixed=fixed;oracle.baseline=dict(origin_nm=[0,0],rotation_deg=0)
    oracle.depth=5;oracle.bounds=[0,0,100,100];oracle.cell_nm=10;oracle.rows={};oracle.columns={}
    for i,feature in enumerate(fixed):
        a,b,c,d=feature['box']
        for row in range(b//10,d//10+1):oracle.rows.setdefault(row,set()).add(i)
        for col in range(a//10,c//10+1):oracle.columns.setdefault(col,set()).add(i)
    oracle.p=NS(VECTOR2I=lambda x,y:(x,y));oracle.material=NS(Contains=lambda p:True)
    result=oracle(dict(origin_nm=[0,0],rotation_deg=0))
    expected=native._space_metrics(core,fixed,oracle.bounds,access_depth_nm=5)
    assert {k:v for k,v in result.items() if k!='queried_fixed_features'}==expected
    assert result['queried_fixed_features']==1


def test_compound_courtyard_islands_leave_the_middle_passage_open():
    def box(values):return NS(GetLeft=lambda:values[0],GetTop=lambda:values[1],GetRight=lambda:values[2],GetBottom=lambda:values[3])
    outlines=[NS(BBox=lambda:box([-55,-5,-45,5])),NS(BBox=lambda:box([45,-5,55,5]))]
    courtyard=NS(OutlineCount=lambda:2,COutline=lambda i:outlines[i])
    extracted=native._courtyard_boxes(courtyard)
    assert extracted==[[-55,-5,-45,5],[45,-5,55,5]]
    core=[dict(box=[-5,-4,5,4],layer=2,kind='body')]
    fixed=[dict(box=b,layer=2,kind='body',ref='STAB_6.25') for b in extracted]
    result=native._space_metrics(core,fixed,[-100,-100,100,100],access_depth_nm=5)
    assert result['legal'] is True and result['side_space_nm']['left']==40 and result['side_space_nm']['right']==40


def test_positive_58um_courtyard_gap_does_not_receive_copper_clearance():
    core=[dict(box=[140000000,73858524,150000000,85000000],layer=2,kind='body',gap_nm=0),
          dict(box=[144000000,74500000,145000000,75000000],layer=2,kind='pad',gap_nm=201000)]
    led=[dict(box=[143000000,70000000,147000000,73800001],layer=2,kind='body',ref='LED48',gap_nm=0)]
    result=native._space_metrics(core,led,[0,0,300000000,100000000],access_depth_nm=1000000)
    assert result['legal'] is True and result['collision_witnesses']==[]
    assert result['side_space_nm']['top']==58523
    assert result['side_witnesses']['top']['ref']=='LED48'
    # The same physical gap between actual copper still needs its declared margin.
    copper=[dict(box=led[0]['box'],layer=2,kind='pad',gap_nm=201000)]
    near_pad=[dict(box=core[0]['box'],layer=2,kind='pad',gap_nm=201000)]
    assert native._space_metrics(near_pad,copper,[0,0,300000000,100000000],access_depth_nm=1000000)['legal'] is False


def test_internal_aux_preserves_exterior_slabs_and_one_face_crossing_only_zeroes_that_face():
    core=[dict(box=[40,40,60,45],layer=2,kind='pad'),dict(box=[40,55,60,60],layer=2,kind='pad')]
    aux=dict(box=[45,47,55,53],layer=2,kind='pad',ref='R_AUX',pad='1')
    measured=native._space_metrics(core,[aux],[0,0,100,100],access_depth_nm=5)
    assert measured['legal'] is True
    assert measured['side_space_nm']==dict(left=40,right=40,top=40,bottom=40)
    assert measured['internal_obstruction_witnesses'][0]['ref']=='R_AUX'
    aux['box']=[35,47,45,53]
    crossed=native._space_metrics(core,[aux],[0,0,100,100],access_depth_nm=5)
    assert crossed['legal'] is True
    assert crossed['side_space_nm']==dict(left=0,right=40,top=40,bottom=40)
    assert crossed['side_witnesses']['left']['ref']=='R_AUX'


def test_whole_core_access_halo_pushes_crossing_support_outside_without_moving_fixed_connector():
    import ast
    from pathlib import Path
    features=[dict(box=[144000000,75000000,155208000,86000000],layer=2,kind='body'),
              dict(box=[145000000,76000000,154000000,85000000],layer=2,kind='pad'),
              dict(box=[145000000,77000000,155500000,84000000],layer=0,kind='copper')]
    halos=native._core_envelope_halos(features,depth_nm=996000)
    by_layer={q['layer']:q for q in halos}
    assert by_layer[2]['box']==[143004000,74004000,156496000,86996000]
    assert by_layer[0]['box']==by_layer[2]['box']
    assert by_layer[2]['core_bounds_nm']==[144000000,75000000,155500000,86000000]
    class Support:
        def __init__(self,x=0,y=0):self.position=(x,y)
        def GetCourtyard(self,layer):return NS(OutlineCount=lambda:0)
        def Rotate(self,centre,angle):assert angle==0
    support=Support();fixed_connector=Support(162000000,83500000);fixed_before=fixed_connector.position
    def clear(objects):
        for obj in objects:
            x,y=obj.position;a,b,c,d=x-1525000,y-775000,x+1525000,y+775000
            for halo in halos:
                if halo['layer']!=2:continue
                left,top,right,bottom=halo['box']
                if a<=right and c>=left and b<=bottom and d>=top:return False
        return True
    support.position=(156000000,82500000)
    assert not clear([support])
    support.position=(0,0)
    path=Path(__file__).resolve().parents[1]/'smilepcb/stm32/place.py'
    function=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='pack')
    def move(objects,delta):
        for obj in objects:obj.position=tuple(obj.position[i]+delta[i] for i in (0,1))
    scope=dict(p=NS(FOOTPRINT=Support,F_Cu=0,B_Cu=2,EDA_ANGLE=lambda a,u:a,DEGREES_T=1),bbox=lambda _:None,
               width=170000000,height=90000000,time=NS(monotonic=lambda:0),stop=1,move=move,clear=clear,vector=lambda x,y:(x,y))
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),scope)
    placed,_=scope['pack']([support],(156000000,82500000),angles=(0,))
    assert placed is not None and support.position[0]-1525000>by_layer[2]['box'][2]
    assert clear([support]) and fixed_connector.position==fixed_before


def test_search_pose_uses_available_minimum_whole_core_halo_and_validates_dimensions():
    assert native._available_halo_radius(dict(origin_nm=[1,2],rotation_deg=0)) is None
    pose=dict(diagnostics=dict(side_space_nm=dict(left=4950195,right=6500000,top=5300000,bottom=7000000)))
    assert native._available_halo_radius(pose)==4950195
    features=[dict(box=[0,0,10,20],layer=2)]
    assert native._core_envelope_halos(features,depth_nm=native._available_halo_radius(pose))[0]['box']==[-4950195,-4950195,4950205,4950215]
    for invalid in (-1,1.5,True,None):
        bad=deepcopy(pose);bad['diagnostics']['side_space_nm']['left']=invalid
        with pytest.raises(ValueError,match='nonnegative integer'):native._available_halo_radius(bad)
    missing=deepcopy(pose);del missing['diagnostics']['side_space_nm']['top']
    with pytest.raises(ValueError):native._available_halo_radius(missing)
    zero=deepcopy(pose);zero['diagnostics']['side_space_nm']['left']=0
    assert native._core_envelope_halos(features,depth_nm=native._available_halo_radius(zero))[0]['box']==[0,0,10,20]
    with pytest.raises(ValueError):native.assembly_controls({'_core_envelope_radius_nm':True})
