"""The Native facade isolates unavailable bindings; geometry admission is real."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import importlib, math, sys
import pytest

M=1000000
class Item:
    def __init__(self,uid,a,b,face=2,kind='PCB_TRACK',width=150000,drill=200000):
        self.m_Uuid=SimpleNamespace(AsString=lambda:uid);self.a=a;self.b=b;self.face=face;self.kind=kind;self.width=width;self.drill=drill;self.locked=False;self.net='signal';self.top=0;self.bottom=2;self.via_type=4
    def GetStart(self):return SimpleNamespace(x=self.a[0],y=self.a[1])
    def GetEnd(self):return SimpleNamespace(x=self.b[0],y=self.b[1])
    def GetClass(self):return self.kind
    def GetNetname(self):return self.net
    def GetLayer(self):return self.face
    def IsLocked(self):return self.locked
    def GetWidth(self,*args):return self.width
    def GetDrillValue(self):return self.drill
    def GetViaType(self):return self.via_type
    def TopLayer(self):return self.top
    def BottomLayer(self):return self.bottom
    def IsOnLayer(self,f):return f in (0,2) if self.kind=='PCB_VIA' else f==self.face
class Pad:
    def GetNumber(self):return '7'
    def GetNetname(self):return 'signal'
    def GetPosition(self):return SimpleNamespace(x=0,y=0)
    def IsOnLayer(self,f):return f==2
    def GetEffectiveShape(self,f):return (-M,-M,M,M)
class Board:
    def __init__(self,items):self.items=items;self.pad=Pad()
    def GetTracks(self):return list(self.items)
    def GetFootprints(self):return [SimpleNamespace(GetReference=lambda:'MCU',Pads=lambda:[self.pad])]
FAKE_P=SimpleNamespace(F_Cu=0,B_Cu=2,VIATYPE_THROUGH=4,VECTOR2I=lambda x,y:(x,y),SHAPE_CIRCLE=lambda q,r:q,SHAPE=SimpleNamespace(Collide=lambda q,box,gap:box[0]<=q[0]<=box[2] and box[1]<=q[1]<=box[3]))

def fixture():
    # Stored stub points toward the pad center, as in the real COL1 case.
    items=[Item('stub',(M//2,0),(0,0)),Item('b',(M//2,0),(3*M//2,M)),Item('via',(3*M//2,M),(3*M//2,M),kind='PCB_VIA',width=620000,drill=300000),Item('f',(3*M//2,M),(9*M,M),face=0)]
    suffix=[(0,[(5*M,M),(9*M,M)])]
    row=dict(net='signal',ids=['stub','b','via','f'],terminal_stub_ids=['stub'],width=150000,mcu_ref='MCU',mcu_pad='7',pad_position=(0,0),heading=(-1,0),old_legs=[(2,[(M//2,0),(3*M//2,M)]),(0,[(3*M//2,M),(9*M,M)])],window=dict(source=(0,0),source_layer=2,target=(5*M,M),target_layer=0,lo_nm=0,hi_nm=math.sqrt(2)*M+3.5*M,prefix_legs=[],suffix_legs=suffix,fixed_legs=suffix,fixed_vias=[],body_vias=[(3*M//2,M)]))
    return Board(items),row

def variant(board,row,**kwargs):
    with patch.dict(sys.modules,{'smilepcb.native_worker':SimpleNamespace(p=FAKE_P)}):
        return importlib.import_module('smilepcb.keyboard_routing.source_prefixes').source_prefix_variant(board,row,**kwargs)

def test_exact_owned_reversed_stub_is_replayed_to_existing_via_without_changing_suffix():
    board,row=fixture();saved=deepcopy(row);out=variant(board,row)
    assert out is not None
    assert out['window']['source']==(3*M//2,M) and out['window']['source_layer']==0
    assert out['window']['prefix_legs']==[(2,[(0,0),(M//2,0),(3*M//2,M)])]
    assert out['window']['fixed_vias']==[(3*M//2,M)] and out['window']['body_vias']==[]
    assert out['window']['suffix_legs']==saved['window']['suffix_legs']
    assert out['window']['target']==saved['window']['target']
    assert out['ids']==saved['ids'] and out['pad_position']==saved['pad_position'] and row==saved
    assert out['source_prefix_witness']['via']['width']==620000
    assert out['source_prefix_witness']['via']['drill']==300000

@pytest.mark.parametrize('change',['moved_via','changed_prefix','unowned_stub','unknown_id','locked','protected','ambiguous_stub','reversed_chain','nonthrough','wrong_pad','wrong_width'])
def test_changed_or_ambiguous_source_witness_is_rejected(change):
    board,row=fixture();kwargs={}
    if change=='moved_via':board.items[2].a=(2*M,M);board.items[2].b=(2*M,M)
    if change=='changed_prefix':board.items[1].a=(M//2+1,0)
    if change=='unowned_stub':row['ids'].remove('stub')
    if change=='unknown_id':row['ids'].append('missing')
    if change=='locked':board.items[1].locked=True
    if change=='protected':kwargs['protected_ids']={'via'}
    if change=='ambiguous_stub':board.items.append(Item('duplicate',(0,0),(M//2,0)));row['ids'].append('duplicate');row['terminal_stub_ids'].append('duplicate')
    if change=='reversed_chain':row['old_legs'][0]=(2,list(reversed(row['old_legs'][0][1])))
    if change=='nonthrough':board.items[2].via_type=1
    if change=='wrong_pad':board.pad.GetEffectiveShape=lambda f:(10*M,10*M,11*M,11*M)
    if change=='wrong_width':board.items[1].width=160000
    assert variant(board,row,**kwargs) is None

def test_collinear_source_segments_may_share_one_simplified_prefix_edge():
    board,row=fixture();board.items[1:2]=[Item('b1',(M//2,0),(M,M//2)),Item('b2',(M,M//2),(3*M//2,M))];row['ids'].remove('b');row['ids']+=['b1','b2']
    assert variant(board,row) is not None

def centered_fixture():
    board,row=fixture();board.items=board.items[1:];board.items[0].a=(0,0)
    row['ids'].remove('stub');row['terminal_stub_ids']=[]
    row['old_legs'][0][1][0]=(0,0)
    row['window']['hi_nm']=math.hypot(1.5*M,M)+3.5*M
    return board,row

def test_a_centered_owned_chain_needs_no_invented_pad_stub():
    board,row=centered_fixture();out=variant(board,row)
    assert out is not None and out['window']['prefix_legs']==[(2,[(0,0),(3*M//2,M)])]
    assert out['source_prefix_witness']['track_ids']==['b']

def test_unknown_terminal_ownership_is_rejected_even_if_no_prepend_is_needed():
    board,row=centered_fixture();row['terminal_stub_ids']=['missing']
    assert variant(board,row) is None

def test_changed_fixed_suffix_is_not_silently_reconstructed():
    board,row=fixture();row['window']['suffix_legs']=deepcopy(row['window']['suffix_legs'])
    row['window']['suffix_legs'][0][1][-1]=(9*M+1,M)
    assert variant(board,row) is None
