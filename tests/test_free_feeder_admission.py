"""Admission contracts with native graph/geometry seams; no KiCad jobs.

The production admission function and launch lifecycle are real. Endpoint
connectivity substitutes for native polygon intersections on these host tests.
"""
from copy import deepcopy
import importlib
import json
from types import SimpleNamespace
import sys

import pytest

from smilepcb.keyboard_routing.interface_launches import (
    current_launch_inventory, current_launches, reserve_launches,
)


class Item:
    def __init__(self, uid, net, start, end=None, *, kind='PCB_TRACK', locked=False, number=None):
        self.uid=uid;self.number=number;self.raw='native-shape'
        self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
        self.data={'class':kind,'net':net,'start':list(start),'end':list(end or start),
                   'width':173000,'layer':0,'locked':locked}
    def GetNetname(self):return self.data['net']
    def GetClass(self):return self.data['class']
    def IsLocked(self):return self.data['locked']


class Pad(Item):
    def GetNumber(self):return self.number


class Footprint:
    def __init__(self, ref, pads):self.ref=ref;self.pads=pads
    def GetReference(self):return self.ref
    def Pads(self):return list(self.pads)


class Board:
    def __init__(self):
        self.tracks={};self.footprints=[];self.artwork='retained-outline'
        self.rules=(200000,250000,173000,250000,200000,450000,100000)
        self.timestamp=0
    def GetTracks(self):return list(self.tracks.values())
    def GetFootprints(self):return list(self.footprints)
    def GetTimeStamp(self):return self.timestamp
    def Zones(self):return []
    def GetDesignSettings(self):return SimpleNamespace(m_TrackMinWidth=173000)


def snapshot(board):
    return {'tracks':{uid:deepcopy(t.data) for uid,t in board.tracks.items()},
            'footprints':{f.ref:{'ref':f.ref,'position':[0,0],
                'pads':[{'uuid':p.uid,'number':p.number,'net':p.GetNetname(),
                         'position':p.data['start'][:]} for p in f.pads]}
                          for f in board.footprints}}


def endpoint_graph(board,nets,deadline):
    items=[q for q in board.GetTracks()+[p for f in board.footprints for p in f.pads] if q.GetNetname() in nets]
    parents=list(range(len(items)))
    def find(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    def touches(a,b):
        def contains(item,point):
            start,end=item.data['start'],item.data['end']
            return (start[1]==end[1]==point[1] and min(start[0],end[0])<=point[0]<=max(start[0],end[0]))
        return any(contains(a,q) for q in (b.data['start'],b.data['end'])) or any(contains(b,q) for q in (a.data['start'],a.data['end']))
    nodes=[{'item':q,'net':q.GetNetname(),'zone':False} for q in items]
    for i,a in enumerate(items):
        for j,b in enumerate(items[:i]):
            if a.GetNetname()==b.GetNetname() and touches(a,b):parents[find(i)]=find(j)
    groups={}
    for i in range(len(items)):groups.setdefault(find(i),[]).append(i)
    return {'nodes':nodes,'find':find,'groups':groups,'nets':set(nets)}


class PhysicalProof:
    def _physical_state(self,excluded_ids=()):
        return repr((self.board.artwork,
            sorted((q.uid,q.raw) for q in self.board.GetTracks() if q.uid not in excluded_ids),
            sorted((p.uid,p.raw) for f in self.board.footprints for p in f.pads)))
    def _rule_state(self):return self.board.rules


@pytest.fixture
def world(monkeypatch):
    w=SimpleNamespace(board=Board(),now=0.,graph_calls=[],proof_calls=[],land_calls=[],
                      graph_hook=None,proof_hook=None,land_hook=None,guard_violation=False)
    row=Pad('mcu-row','ROW_A',(0,0),kind='PAD',number='1')
    col=Pad('mcu-col','COL_B',(0,10),kind='PAD',number='2')
    aux=Pad('mcu-aux','AUX',(0,20),kind='PAD',number='3')
    ground=Pad('mcu-ground','GND',(0,30),kind='PAD',number='4')
    w.board.footprints=[Footprint('U_MCU',[row,col,aux,ground]),
        Footprint('SW1',[Pad('field-row','ROW_A',(10,0),kind='PAD',number='1'),
                          Pad('alias-row','ROW_A',(10,0),kind='PAD',number='1'),
                          Pad('field-col','COL_B',(10,10),kind='PAD',number='2')])]
    for uid,net,a,b in [('fixed-head','AUX',(0,20),(5,20)),
                         ('field-tail','ROW_A',(8,0),(10,0))]:
        w.board.tracks[uid]=Item(uid,net,a,b,locked=True)
    w.retained=snapshot(w.board)
    w.physical=PhysicalProof._physical_state(SimpleNamespace(board=w.board))
    w.rules=w.board.rules
    w.board.tracks['row-feeder']=Item('row-feeder','ROW_A',(0,0),(8,0))
    w.board.tracks['col-feeder']=Item('col-feeder','COL_B',(0,10),(10,10))
    w.ports=[{'net':'AUX','pad_ids':['mcu-aux'],'added_ids':['fixed-head'],
              'terminal_id':'fixed-head','kind':'same_layer','via_id':None,'via_nm':None,
              'exit_nm':[5,20],'exit_layer':0,'heading_origin_nm':[0,20]}]
    w.cfg={'parts':[{'ref':'U_MCU','region':'controller',
                     'nets':{'1':'ROW_A','2':'COL_B','3':'AUX','4':'GND'}},
                    {'ref':'SW1','region':'field','nets':{'1':'ROW_A','2':'COL_B'}}],
           'layout':{'gpio':{'rows':['R'],'cols':['C']},'rgb':False},
           'qmk':{'matrix_pins':{'rows':['R'],'cols':['C']}},
           'mcu_gpio_pads':{'R':'1','C':'2'},'plane_nets':['GND']}
    def graph(board,nets,deadline):
        w.graph_calls.append((set(nets),deepcopy(current_launches()),deadline))
        result=endpoint_graph(board,nets,deadline)
        return w.graph_hook(result) if w.graph_hook else result
    def prove(board,added,graph,covered,deadline):
        w.proof_calls.append((set(added),set(covered),deepcopy(current_launches()),deadline))
        if w.guard_violation and any(q['net']=='AUX' for q in current_launches()):
            raise ValueError('native fixed-target guard collision')
        if w.proof_hook:w.proof_hook()
        return len(added)
    def lands(board,deadline=None):
        w.land_calls.append(deadline)
        if w.land_hook:w.land_hook()
        return {'vias_checked':0}
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(
        p=SimpleNamespace(PAD=Pad,F_Cu=0,B_Cu=2),snapshot=snapshot))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',SimpleNamespace(copper_graph=graph))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.conditional_geometry',SimpleNamespace(
        FrozenProofRouter=PhysicalProof,prove_copper=prove))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.safety',SimpleNamespace(require_via_land_clearance=lands))
    def run():
        try:module=importlib.import_module('smilepcb.keyboard_routing.free_feeder_admission')
        except ModuleNotFoundError as error:
            if error.name=='smilepcb.keyboard_routing.free_feeder_admission':pytest.fail('Missing native free-matrix admission helper')
            raise
        monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:w.now))
        return module.prove_free_matrix(w.board,w.cfg,w.retained,fixed_ports=w.ports,
                    physical_seal=w.physical,rule_seal=w.rules,deadline=100.)
    w.run=run
    return w


def test_closed_matrix_uses_full_inventory_and_returns_serializable_report(world):
    before=deepcopy((snapshot(world.board),world.retained,world.cfg,world.ports))
    result=world.run()
    assert result=={'accepted':True,'matrix_groups':{'ROW_A':1,'COL_B':1},
                    'creation_checked_items':2,'numbered_pad_count':5,'mutable_copper_count':2}
    json.dumps(result)
    assert world.graph_calls==[({'ROW_A','COL_B','AUX','GND'},tuple(world.ports),100.)]
    assert world.proof_calls==[({'row-feeder','col-feeder'},{'ROW_A','COL_B','AUX','GND'},tuple(world.ports),100.)]
    assert world.land_calls==[100.]
    assert (snapshot(world.board),world.retained,world.cfg,world.ports)==before


@pytest.mark.parametrize('mode',['missing-alias','wrong-pad-net','missing-feeder','wrong-feeder-net'])
def test_graph_must_cover_exact_physical_matrix_inventory(world,mode):
    def corrupt(graph):
        target='alias-row' if 'pad' in mode or 'alias' in mode else 'row-feeder'
        for node in graph['nodes']:
            if node['item'].uid==target:
                if mode.startswith('wrong'):node['net']='AUX'
                else:graph['nodes'].remove(node)
                break
        return graph
    world.graph_hook=corrupt
    with pytest.raises(ValueError):world.run()


def test_connected_pads_do_not_license_detached_mutable_copper(world):
    world.board.tracks['orphan']=Item('orphan','ROW_A',(30,0),(40,0))
    with pytest.raises(ValueError,match='(?i)(orphan|disconnected|component)'):world.run()


@pytest.mark.parametrize('mode',['foreign','locked','unsupported','changed-retained','deleted-retained','pad-net','footprint'])
def test_nonmatrix_locked_or_retained_changes_are_rejected(world,mode):
    if mode=='foreign':world.board.tracks['row-feeder'].data['net']='AUX'
    elif mode=='locked':world.board.tracks['row-feeder'].data['locked']=True
    elif mode=='unsupported':world.board.tracks['row-feeder'].data['class']='PCB_ARC'
    elif mode=='changed-retained':world.board.tracks['field-tail'].data['width']=999
    elif mode=='deleted-retained':del world.board.tracks['field-tail']
    elif mode=='pad-net':world.board.footprints[1].pads[0].data['net']='AUX'
    else:world.board.footprints[1].ref='MOVED'
    before=snapshot(world.board)
    with pytest.raises(ValueError):world.run()
    assert snapshot(world.board)==before


@pytest.mark.parametrize('mode',['pad-model','firmware','raw-footprint','raw-retained','artwork','rules'])
def test_model_and_raw_native_seals_are_authoritative(world,mode):
    if mode=='pad-model':world.cfg['parts'][1]['nets']['1']='AUX'
    elif mode=='firmware':world.cfg['qmk']['matrix_pins']['rows']=['C']
    elif mode=='raw-footprint':world.board.footprints[1].pads[0].raw='custom-pad-changed'
    elif mode=='raw-retained':world.board.tracks['field-tail'].raw='clearance-changed'
    elif mode=='artwork':world.board.artwork='outline-changed'
    else:world.board.rules=(0,)*7
    with pytest.raises(ValueError):world.run()


@pytest.mark.parametrize('mode',['matrix-port','missing-pad','missing-head','wrong-head-net','invented-exit','disconnected-source'])
def test_only_authentic_retained_fixed_ports_enter_the_scope(world,mode):
    port=world.ports[0]
    if mode=='matrix-port':port['net']='ROW_A'
    elif mode=='missing-pad':port['pad_ids']=['absent']
    elif mode=='missing-head':port['added_ids']=['absent']
    elif mode=='wrong-head-net':port['added_ids']=['field-tail'];port['terminal_id']='field-tail'
    elif mode=='invented-exit':port['exit_nm']=[99,20]
    else:
        world.board.tracks['fixed-head'].data['start']=[1,20]
        world.retained=snapshot(world.board)
        for uid in ('row-feeder','col-feeder'):del world.retained['tracks'][uid]
        port['heading_origin_nm']=[1,20]
    with pytest.raises(ValueError):world.run()


def test_fixed_target_guard_failure_is_not_hidden_by_outer_matrix_launches(world):
    world.guard_violation=True
    outer=[{'net':'ROW_A','pad_ids':['mcu-row'],'added_ids':['row-feeder']}]
    with reserve_launches(outer):
        state=deepcopy(current_launch_inventory().__dict__)
        with pytest.raises(ValueError,match='fixed-target'):world.run()
        assert current_launch_inventory().__dict__==state
    assert world.proof_calls[0][2]==tuple(world.ports)


@pytest.mark.parametrize('stage',['entry','graph','creation','land'])
def test_deadline_expiry_rejects_even_a_connected_board(world,stage):
    if stage=='entry':world.now=100.
    elif stage=='graph':
        def expired(graph):world.now=100.;return graph
        world.graph_hook=expired
    else:setattr(world,stage.replace('creation','proof')+'_hook',lambda:setattr(world,'now',100.))
    with pytest.raises(TimeoutError):world.run()
    if stage=='entry':assert world.graph_calls==[]


@pytest.mark.parametrize('stage',['graph','creation','land'])
@pytest.mark.parametrize('mutation',['snapshot','physical','rules'])
def test_native_queries_cannot_mutate_the_admitted_frame(world,stage,mutation):
    def change():
        if mutation=='snapshot':world.board.tracks['row-feeder'].data['width']=999
        elif mutation=='physical':world.board.tracks['row-feeder'].raw='hidden-native-edit'
        else:world.board.rules=(1,)*7
    if stage=='graph':
        def changed(graph):change();return graph
        world.graph_hook=changed
    else:setattr(world,stage.replace('creation','proof')+'_hook',change)
    with pytest.raises(ValueError):world.run()


def test_independent_via_land_failure_is_required(world):
    def reject():raise ValueError('via solder land collision')
    world.land_hook=reject
    with pytest.raises(ValueError,match='solder land'):world.run()


@pytest.mark.parametrize('uid',['mcu-ground','field-tail'])
def test_graph_cannot_omit_unadvertised_retained_conductors(world,uid):
    def omit(graph):
        keep=[(index,node) for index,node in enumerate(graph['nodes']) if node['item'].uid!=uid]
        roots=[graph['find'](index) for index,_ in keep]
        graph['nodes']=[node for _,node in keep];graph['find']=lambda index:roots[index]
        return graph
    world.graph_hook=omit
    with pytest.raises(ValueError):world.run()


def test_unnumbered_matrix_pad_cannot_be_an_orphan(world):
    world.board.footprints[1].pads.append(Pad('unnumbered','ROW_A',(99,0),kind='PAD',number=''))
    world.retained['footprints']=snapshot(world.board)['footprints']
    world.physical=PhysicalProof._physical_state(SimpleNamespace(board=world.board),
                                               excluded_ids={'row-feeder','col-feeder'})
    with pytest.raises(ValueError,match='(?i)(orphan|disconnected|component)'):world.run()


def test_mutable_via_is_part_of_native_inventory_and_creation_proof(world):
    via=Item('matrix-via','ROW_A',(4,0),kind='PCB_VIA')
    via.data.update(drill=200000,top=0,bottom=2,via_type=3)
    world.board.tracks[via.uid]=via
    report=world.run()
    assert report['mutable_copper_count']==report['creation_checked_items']==3
    assert world.proof_calls[0][0]=={'row-feeder','col-feeder','matrix-via'}


def test_creation_proof_must_account_for_every_mutable_item(world,monkeypatch):
    geometry=sys.modules['smilepcb.keyboard_routing.conditional_geometry']
    monkeypatch.setattr(geometry,'prove_copper',lambda *args:1)
    with pytest.raises(ValueError):world.run()
