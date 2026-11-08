"""HOST placement-entry regressions; native geometry admission is separate."""
import ast
import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from smilepcb.keyboard_placement import rgb_chain

ROOT=Path(__file__).resolve().parents[1]


class Pad:
    def __init__(self,number,position):self.number=number;self.position=position;self.net=None
    def GetNumber(self):return self.number
    def GetPosition(self):return SimpleNamespace(x=self.position[0],y=self.position[1])
    def SetNet(self,net):self.net=net


class EmptyCourtyard:
    def OutlineCount(self):return 0
    def BooleanAdd(self,other):pass


class Footprint:
    def __init__(self,ref,position,recipe=180,side='B.Cu',pads=None):
        self.ref=ref;self.position=position;self.side=side;self.m_Uuid=SimpleNamespace(AsString=lambda:ref)
        # Native backside orientation is 180 - recipe angle. It is not the
        # recipe angle, and the MCU's unrelated native pose must be ignored.
        self.angle=(180-recipe)%360 if side=='B.Cu' else recipe
        offsets={'3':(2725000,-750000),'2':(-2725000,750000)}
        self.pads=[Pad(k,(position[0]+(x if recipe==0 else -x),position[1]+(y if recipe==0 else -y)))
                   for k,(x,y) in offsets.items()] if pads is None else pads
    def GetCourtyard(self,layer):return EmptyCourtyard()
    def Duplicate(self,_):return deepcopy(self)
    def GetReference(self):return self.ref
    def GetPosition(self):return SimpleNamespace(x=self.position[0],y=self.position[1])
    def GetLayer(self):return self.side
    def GetOrientationDegrees(self):return self.angle
    def Pads(self):return self.pads
    def Rotate(self,centre,angle):
        assert (centre.x,centre.y)==self.position and angle in (-180,0,180)
        if angle:
            for pad in self.pads:pad.position=tuple(2*self.position[i]-pad.position[i] for i in (0,1))
        self.angle=(self.angle+angle)%360


class Board:
    def __init__(self,footprints):self.footprints=footprints
    def GetFootprints(self):return self.footprints
    def GetLayerName(self,layer):return layer


def placement_final_block():
    # Run the actual normal place.py accepted-placement block. Only the
    # external native board/packing boundary is replaced, not chain or poses.
    tree=ast.parse((ROOT/'smilepcb/stm32/place.py').read_text())
    outer=next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=='pose')
    accepted=next(n for n in outer.body if isinstance(n,ast.If) and ast.unparse(n.test)=='not failed')
    end=next(i for i,n in enumerate(accepted.body) if isinstance(n,ast.ImportFrom)
             and n.module=='smilepcb.keyboard_routing.generated_provenance')
    return compile(ast.fix_missing_locations(ast.Module(body=accepted.body[:end],type_ignores=[])),str(ROOT/'smilepcb/stm32/place.py'),'exec')


def placed(*,right=True,driver_pad=None):
    keys=[{'id':i+1,'center_mm':[i*20,0],'matrix':[0,i]} for i in range(3)]
    led=[Footprint('LED'+str(i+1),(i*20000000,5080000),recipe=180) for i in range(3)]
    x=60000000 if right else -20000000
    driver=Footprint('R_RGB',(x,5080000),pads=[Pad('2',driver_pad or (x-500000,5080000))])
    mcu=Footprint('U_MCU',(75000000,10000000),recipe=0,pads=[])
    board=Board([*led,driver,mcu]);parts=[]
    for f in board.GetFootprints():
        parts.append({'ref':f.ref,'pcb':[f.position[0]/1e6,f.position[1]/1e6],
                      'side':f.side,'rotation':f.angle,'nets':{'2':'old-out','3':'old-in'}})
    cfg={'layout':{'keys':keys,'rgb':True,'rgb_order':[1,2,3],'size_mm':[60,20]},
         'parts':parts,'qmk':{'rgb_matrix':{'layout':[]}}}
    checks=[]
    source=SimpleNamespace(GetPosition=lambda:SimpleNamespace(x=0,y=0),Pads=lambda:[Pad('3',(-2725000,-750000)),Pad('2',(2725000,750000))])
    env=dict(project=ROOT,cfg=cfg,board=board,removed=set(),parts=[],width=60000000,height=20000000,
             controls={'exact_pose':None,'assembly_only':False,'fixed_pose':None},
             rgb_chain=rgb_chain,time=time,net=lambda name:name,stop=float('inf'),
             p=SimpleNamespace(EDA_ANGLE=lambda value,unit:value,DEGREES_T=1,F_Cu='F.Cu',B_Cu='B.Cu',SHAPE_POLY_SET=deepcopy,Cast_to_FOOTPRINT=lambda f:f,FootprintLoad=lambda folder,name:source),
             rebuild_obstacles=lambda:checks.append('fresh-index'),
             clear=lambda objects,**kwargs:checks.append([f.ref for f in objects]) or True)
    return env,checks


@pytest.mark.parametrize('right,order,native_angle',[(True,[3,2,1],180),(False,[1,2,3],0)])
def test_normal_placement_reorients_after_final_cut_and_before_controller_copper(right,order,native_angle):
    env,checks=placed(right=right);before=deepcopy(env['cfg']);other=env['board'].footprints[-2:]
    physical_before=[(f.position,f.angle,tuple(p.position for p in f.Pads())) for f in other]
    exec(placement_final_block(),env)
    assert env['cfg']['layout']['rgb_order']==order
    for f in env['board'].footprints[:3]:
        assert f.angle==native_angle  # stale initial 180-recipe gives native0
        part=next(p for p in env['cfg']['parts'] if p['ref']==f.ref)
        assert part['rotation']==native_angle
        assert part['pcb']==next(p['pcb'] for p in before['parts'] if p['ref']==f.ref)
    assert checks==['fresh-index',['LED'+str(i) for i in order]]
    assert [(f.position,f.angle,tuple(p.position for p in f.Pads())) for f in other]==physical_before
    by_ref={p['ref']:p for p in env['cfg']['parts']}
    assert by_ref['LED'+str(order[0])]['nets']['3']=='RGB_0'
    assert by_ref['LED'+str(order[-1])]['nets']['2']=='NC_RGB_END'
    assert [row['matrix'] for row in env['cfg']['qmk']['rgb_matrix']['layout']]==[[0,i-1] for i in order]


def test_normal_placement_reports_clearance_rejection_instead_of_aborting_pose_search(tmp_path,capsys):
    env,checks=placed();env['clear']=lambda _,**kwargs:False
    env.update(project=tmp_path,json=json,result={'stage':'placement','status':'incomplete'},started=time.monotonic())
    saved=[];env['p'].SaveBoard=lambda path,board:saved.append(Path(path).name) or True
    with pytest.raises(SystemExit) as exit:exec(placement_final_block(),env)
    assert exit.value.code==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='access-rejected' and result['rejection_reason']=='final-led-placement-clearance'
    assert saved==['rejected-placement.kicad_pcb'] and checks==['fresh-index']



def test_final_placement_uses_driver_output_pad_not_resistor_centre():
    env,checks=placed(right=True,driver_pad=(-20000000,5080000))
    board=env['board'];led=Footprint('LED1',(0,5080000),recipe=0)
    board.footprints=[led,*board.footprints[-2:]]
    env['cfg']['parts']=[p for p in env['cfg']['parts'] if p['ref'] not in ('LED2','LED3')]
    env['cfg']['layout']['keys']=env['cfg']['layout']['keys'][:1]
    env['cfg']['layout']['rgb_order']=[1]
    exec(placement_final_block(),env)
    assert led.angle==0  # DIN west for the actual pad2 lead; centre is east
    assert led.position==(0,5080000)


@pytest.mark.parametrize('failure',['expired','unexpected-pads','mixed-faces'])
def test_final_pose_planning_fails_before_rotating_any_led(failure):
    from smilepcb.stm32.final_led_placement import orient_final_led_chain
    env,_=placed();board=env['board'];lookup={f.ref:f for f in board.footprints}
    if failure=='expired':env['stop']=0
    elif failure=='unexpected-pads':lookup['LED2'].pads[0].position=(1,2)
    else:lookup['LED2'].side='F.Cu'
    before=[(f.angle,f.position,tuple(p.position for p in f.Pads())) for f in board.footprints]
    with pytest.raises(TimeoutError if failure=='expired' else ValueError):
        orient_final_led_chain(board,env['cfg'],lookup,p=env['p'],deadline=env['stop'],data_offsets=((-2725000,-750000),(2725000,750000)))
    assert [(f.angle,f.position,tuple(p.position for p in f.Pads())) for f in board.footprints]==before


@pytest.mark.parametrize('obstacle_owner,want',[('led-uid',True),('foreign-uid',False),(None,False)])
def test_fresh_placement_oracle_excludes_only_the_same_footprint_uuid(obstacle_owner,want):
    # Collision truth is supplied by the native boundary. Exercise the real
    # oracle's ownership branch without claiming a mock is physical geometry.
    tree=ast.parse((ROOT/'smilepcb/stm32/place.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='clear')
    shape=object()
    class Copper:
        def IsOnLayer(self,layer):return layer=='B'
        def GetEffectiveShape(self,layer):return shape
        def GetNetname(self):return 'RGB_0'
    class Package:
        m_Uuid=SimpleNamespace(AsString=lambda:'led-uid')
        def GetCourtyard(self,layer):return EmptyCourtyard()
        def Pads(self):return [Copper()]
    class Via:pass
    env=dict(p=SimpleNamespace(FOOTPRINT=Package,F_Cu='F',B_Cu='B',PCB_VIA=Via,
                              SHAPE=SimpleNamespace(Collide=lambda *_:True)),
             rules=SimpleNamespace(m_SilkClearance=0),silkscreen_shapes=lambda _:[],
             inside=lambda _:True,bbox=lambda _:(0,0,1,1),box_overlap=lambda *_:True,
             nearby=lambda *_:[(shape,(0,0,1,1),'smd','foreign',obstacle_owner)])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'place-oracle','exec'),env)
    assert env['clear']([Package()]) is want


def accepted_placement_block():
    tree=ast.parse((ROOT/'smilepcb/stm32/place.py').read_text())
    outer=next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=='pose')
    accepted=next(n for n in outer.body if isinstance(n,ast.If) and ast.unparse(n.test)=='not failed')
    return compile(ast.fix_missing_locations(ast.Module(body=[*accepted.body,*tree.body[-2:]],type_ignores=[])),'normal-accepted-placement','exec')


def st40_model():
    from smilepcb.keyboard_layout import parse_layout
    from smilepcb.keyboard_model import design
    fixture=ROOT/'smilepcb/stm32/inputs/st40-grid48'
    return design(parse_layout((fixture/'layout.kle.json').read_text(),json.loads((fixture/'keyboard.json').read_text())),controller_origin_mm=(0,0))


def test_actual_st40_no_rgb_model_completes_normal_accepted_placement_without_rgb_calls(tmp_path,capsys):
    cfg=st40_model();qmk=deepcopy(cfg['qmk']);incoming_order=list(cfg['layout']['rgb_order'])
    assert cfg['layout']['rgb'] is False and len(cfg['layout']['rgb_order'])==48
    assert not any(p['ref'].startswith('LED') or p['ref']=='R_RGB' for p in cfg['parts'])
    fps=[Footprint(part['ref'],tuple(round(v*1e6) for v in part['pcb']),recipe=0,side=part['side'],pads=[])
         for part in cfg['parts'] if not part['ref'].startswith('#')]
    board=Board(fps);calls=[];board.BuildConnectivity=lambda:calls.append('connectivity')
    def forbidden(*args,**kwargs):raise AssertionError('No RGB recipe/access expected')
    (tmp_path/'fp-lib-table').write_text('(fp_lib_table)')
    def save_board(path,board):
        calls.append(Path(path).name)
        Path(path).write_text('(kicad_pcb)')
        return True
    env=dict(cfg=cfg,board=board,removed=set(),parts=[],width=228600000,height=76200000,
             controls={'exact_pose':None,'assembly_only':False,'fixed_pose':None},
             project=tmp_path,json=json,time=time,Path=Path,started=time.monotonic(),stop=float('inf'),
             request={},result={'stage':'placement','status':'incomplete'},source=SimpleNamespace(GetTracks=lambda:[]),
             rgb_chain=forbidden,net=forbidden,rebuild_obstacles=forbidden,clear=forbidden,
             p=SimpleNamespace(FootprintLoad=forbidden,SaveBoard=save_board),
             require_via_land_clearance=lambda board:calls.append('via-land'))
    exec(accepted_placement_block(),env)
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='placed' and result['model']['layout']['rgb_order']==incoming_order
    assert result['model']['qmk']==qmk and 'rgb_matrix' not in qmk
    assert calls==['baseline.kicad_pcb','via-land','connectivity','keyboard.kicad_pcb']
    assert json.loads((tmp_path/'.smilepcb-keyboard.json').read_text())['qmk']==qmk


def test_actual_st40_no_rgb_outline_emits_only_outer_contour_without_loading_led_recipe():
    cfg=st40_model();items=[]
    class Edge:
        def __init__(self,board):pass
        def SetShape(self,value):pass
        def SetStart(self,value):pass
        def SetEnd(self,value):pass
        def SetLayer(self,value):pass
        def SetWidth(self,value):pass
    def forbidden(*args):raise AssertionError('No LED aperture recipe expected')
    board=SimpleNamespace(GetFootprints=lambda:[],Add=items.append)
    env=dict(cfg=cfg,board=board,project=ROOT,aperture_owners={},
             p=SimpleNamespace(PCB_SHAPE=Edge,SHAPE_T_SEGMENT=1,Edge_Cuts=44,FootprintLoad=forbidden),
             w=SimpleNamespace(point=lambda value:value))
    tree=ast.parse((ROOT/'smilepcb/stm32/place.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='add_outlines')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'normal-placement-outline','exec'),env)
    env['add_outlines']()
    assert len(items)==4 and env['aperture_owners']=={}


def test_coordinator_records_real_rejected_placement_and_reaches_next_pose(tmp_path):
    import io,sys
    from contextlib import redirect_stdout
    tree=ast.parse((ROOT/'smilepcb/stm32/generate.py').read_text())
    pipeline=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_pipeline')
    loop=next(n for n in pipeline.body if isinstance(n,ast.For) and ast.unparse(n.target)=='index')
    code=compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),'normal-generation-pose-loop','exec')
    inputs=tmp_path/'inputs';inputs.mkdir()
    for name in ('layout.kle.json','keyboard.json'):(inputs/name).write_text('[]' if name.startswith('layout') else '{}')
    class NextPoseReached(Exception):pass
    def worker(script,request,log,**kwargs):
        project=Path(request['project'])
        if project.parent.name=='placement-1':raise NextPoseReached
        env,_=placed();env['clear']=lambda _,**kwargs:False
        env.update(project=project,json=json,result={'stage':'placement','status':'incomplete',
            'generation_profile':'dsn-process-v2','core_face':'B.Cu','core_pose':{'origin_nm':[1000000,2000000],'rotation_deg':180}},started=time.monotonic())
        env['p'].SaveBoard=lambda path,board:Path(path).write_text('HOST rejected native-boundary fixture') or True
        output=io.StringIO()
        with redirect_stdout(output):
            try:exec(placement_final_block(),env)
            except SystemExit as exit:assert exit.code==0
        return json.loads(output.getvalue())
    cfg,_=placed()
    scope=dict(args=SimpleNamespace(placement_attempts=2,placement_budget_s=220,routing_candidates=1,native_staged=True,
                 preserve_core_routing=False,optimize_matrix_gpio=True,joint_controller=False,joint_board=False,
                 process_profile='fr4-2l-1oz-0p5-0p2',routing_quality='turns'),
               out=tmp_path,inputs=inputs,core_file=tmp_path/'core',fixture='fixture',normalized='normalized',
               design=lambda *a,**k:deepcopy(cfg['cfg']),parse_layout=lambda *a:None,
               create_libraries=lambda project:None,digest=lambda path:'host-core-fixture',
               time=time,json=json,sys=sys,worker=worker,scripts=ROOT/'smilepcb/stm32',
               STAGED_GENERATION_PROFILE='dsn-process-v2',raw_cfg=None,module_project=None,prepared_core=None,accepted=None,rejected={False:[],True:[]},attempts=[],routed=[],base_angle=None,started=time.monotonic())
    with pytest.raises(NextPoseReached):exec(code,scope)
    attempts=json.loads((tmp_path/'placement-attempts.json').read_text())
    assert len(attempts)==1 and attempts[0]['status']=='access-rejected'
    assert scope['rejected'][False]==[[1000000,2000000,180]]
    assert (tmp_path/'placement-0/project/rejected-placement.kicad_pcb').exists()
    assert (tmp_path/'placement-1/project').is_dir()


def test_unregistered_native_body_geometry_is_an_invariant_error_not_a_pose_rejection(monkeypatch):
    from smilepcb.stm32 import final_led_placement
    monkeypatch.setattr(final_led_placement,'same_courtyard',lambda *a,**k:False)
    class Package:
        m_Uuid=SimpleNamespace(AsString=lambda:'led-uid')
        def GetCourtyard(self,layer):return SimpleNamespace(OutlineCount=lambda:1)
    tree=ast.parse((ROOT/'smilepcb/stm32/place.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='clear')
    env=dict(p=SimpleNamespace(FOOTPRINT=Package,F_Cu='F',B_Cu='B'),
             rules=SimpleNamespace(m_SilkClearance=0),silkscreen_shapes=lambda _:[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'actual-clear-body-binding','exec'),env)
    with pytest.raises(ValueError,match='supported native poses'):
        env['clear']([Package()],led_mounts={'led-uid':{'F':{'poses':[object()],'envelope':object()}}})
