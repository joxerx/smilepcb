"""Ownership selection tests; Native extraction and shapes have small facades."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import importlib, math, sys
import pytest
from smilepcb.keyboard_routing.polish_geometry import chains

M=1000000

def distance(q,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];norm=dx*dx+dy*dy
    t=max(0,min(1,((q[0]-a[0])*dx+(q[1]-a[1])*dy)/norm)) if norm else 0
    return math.dist(q,(a[0]+t*dx,a[1]+t*dy))

def collide(a,b,gap=0):
    if a[0]=='point':
        q=a[1]
        if b[0]=='point':return q==b[1]
        if b[0]=='circle':return math.dist(q,b[1])<=b[2]+gap
        return distance(q,b[1],b[2])<=b[3]/2+gap
    if b[0]=='point':return collide(b,a,gap)
    if a[0]=='circle':
        if b[0]=='circle':return math.dist(a[1],b[1])<=a[2]+b[2]+gap
        return distance(a[1],b[1],b[2])<=a[2]+b[3]/2+gap
    if b[0]=='circle':return collide(b,a,gap)
    return min(distance(q,b[1],b[2]) for q in a[1:3])<=(a[3]+b[3])/2+gap or min(distance(q,a[1],a[2]) for q in b[1:3])<=(a[3]+b[3])/2+gap

class Item:
    def __init__(self,uid,a,b,net='plane',face=0,kind='PCB_TRACK'):
        self.m_Uuid=SimpleNamespace(AsString=lambda:uid);self.a=a;self.b=b;self.net=net;self.face=face;self.kind=kind;self.locked=False
    def GetClass(self):return self.kind
    def GetNetname(self):return self.net
    def GetLayer(self):return self.face
    def GetWidth(self,*args):return 150000
    def IsLocked(self):return self.locked
    def IsOnLayer(self,f):return self.kind=='PCB_VIA' or f==self.face
    def GetEffectiveShape(self,f):return ('circle',self.a,250000) if self.kind=='PCB_VIA' else ('segment',self.a,self.b,150000)
class Zone:
    def __init__(self,uid='zone',net='plane',rule=False):self.m_Uuid=SimpleNamespace(AsString=lambda:uid);self.net=net;self.rule=rule
    def GetNetname(self):return self.net
    def GetIsRuleArea(self):return self.rule
    def IsOnLayer(self,f):return f==0
class Board:
    def __init__(self,items):self.items=items;self.zones=[Zone()];self.pads=[]
    def GetTracks(self):return list(self.items)
    def Zones(self):return list(self.zones)
    def GetFootprints(self):return [SimpleNamespace(Pads=lambda:list(self.pads))]
FAKE_P=SimpleNamespace(F_Cu=0,B_Cu=2,VECTOR2I=lambda x,y:(x,y),SHAPE_CIRCLE=lambda q,r:('point',q),SHAPE=SimpleNamespace(Collide=collide))

def fixture():
    return Board([Item('long',(0,0),(2*M,0)),Item('stub',(2*M,0),(3*M,0)),Item('anchor',(0,0),(0,0),kind='PCB_VIA'),Item('back',(-M,0),(0,0),face=2)])

def select(board,witnesses,protected=()):
    # Real pure chain assembly supplies complete rows, with vias as boundaries.
    def extract(b,**kw):
        assert kw['min_length_nm']==0 and kw['max_points']>=1024
        assert set(kw['deferred_zone_ids'])=={z.m_Uuid.AsString() for z in b.Zones() if not z.GetIsRuleArea()}
        rows={q.m_Uuid.AsString():dict(net=q.net,layer=q.face,width=150000,start=q.a,end=q.b) for q in b.items if q.kind=='PCB_TRACK' and q.net in kw['eligible_nets'] and not q.locked and q.m_Uuid.AsString() not in kw['protected_ids']}
        anchors={(q.net,f,q.a) for q in b.items if q.kind=='PCB_VIA' for f in (0,2)}
        return chains(rows,anchors)
    with patch.dict(sys.modules,{'smilepcb.native_worker':SimpleNamespace(p=FAKE_P),'smilepcb.keyboard_routing.bundle_polish':SimpleNamespace(extract_chains=extract)}):
        return importlib.import_module('smilepcb.keyboard_routing.plane_branches').candidate_plane_branches(board,witnesses,protected_ids=protected)

def test_collision_owns_complete_padless_branch_and_preserves_anchoring_via():
    board=fixture();before=[(q.a,q.b,q.kind,q.net) for q in board.items]
    out=select(board,{'long'})
    assert out is not None and out['removed_ids']==['long','stub']
    proof=out['ownership'][0]
    assert proof['witness_ids']==['long'] and proof['zone_ids']==['zone']
    assert proof['anchor_ids']==['anchor'] and proof['free_end']==(3*M,0)
    assert out['requires_filled_connectivity_proof'] is True
    assert before==[(q.a,q.b,q.kind,q.net) for q in board.items]

@pytest.mark.parametrize('change',['unknown','unwitnessed','nonplane','rule_zone','locked','protected','pad_contact','two_anchors','floating','interior_contact','usb'])
def test_unowned_or_nonbranch_collision_cannot_expand_the_workset(change):
    board=fixture();witness={'long'};protected=()
    if change=='unknown':witness={'missing'}
    if change=='unwitnessed':witness=set()
    if change=='nonplane':board.zones=[]
    if change=='rule_zone':board.zones[0].rule=True
    if change=='locked':board.items[0].locked=True
    if change=='protected':protected={'long'}
    if change=='pad_contact':board.pads=[Item('pad',(M,0),(M,0),kind='PAD')]
    if change=='two_anchors':board.items.append(Item('other',(3*M,0),(3*M,0),kind='PCB_VIA'))
    if change=='floating':board.items=[q for q in board.items if q.kind!='PCB_VIA']
    if change=='interior_contact':board.items.append(Item('inside',(M,0),(M,0),kind='PCB_VIA'))
    if change=='usb':
        board.zones[0].net='USB_DP'
        for q in board.items:q.net='USB_DP'
    assert select(board,witness,protected) is None

def test_invalid_second_witness_does_not_silently_return_a_partial_workset():
    assert select(fixture(),{'long','anchor'}) is None

def test_a_padless_junction_branch_keeps_both_retained_anchor_arms():
    board=fixture();board.items=[q for q in board.items if q.kind!='PCB_VIA' and q.face==0]
    board.items.extend([Item('left',(-M,0),(0,0)),Item('up',(0,-M),(0,0))])
    out=select(board,{'long','stub'})
    assert out is not None and out['removed_ids']==['long','stub']
    assert out['ownership'][0]['anchor_ids']==['left','up']

@pytest.mark.parametrize('protected', [False,True])
def test_a_nonwitnessed_locked_or_protected_continuation_is_not_owned(protected):
    board=fixture()
    if protected:blocked={'stub'}
    else:board.items[1].locked=True;blocked=()
    assert select(board,{'long'},blocked) is None
