"""Creator-bound controller participants; HOST actual endpoint connectivity only."""
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace as NS
import sys,time
import pytest
from test_transient_generated_access import kernel
from smilepcb.keyboard_routing.generation import generation_profile


def scene(monkeypatch, *, local=False):
    net='signal-control' if local else 'return-plane'
    def pad(uid,number,position):return dict(uuid=uid,number=number,net=net,position=position,size=[400000,400000],layers='F.Cu')
    pads=[pad('controller-pad','1',[0,0]),pad('controller-load','1',[2000000,0]),pad('unrelated-pad','1',[10000000,0])]
    fps={name:dict(ref=ref,pads=[p]) for name,ref,p in zip(('chip','load','field'),('U_MCU','C_CTRL','LED7'),pads)}
    def track(net,a,b):return dict(net=net,start=a,end=b,width=150000,layer=0,locked=False,**{'class':'PCB_TRACK'})
    before=dict(footprints=fps,tracks={'imported':track('supply',[0,3000000],[1000000,3000000]),'seed':track('return-plane',[0,4000000],[1000000,4000000])})
    board=NS(state=deepcopy(before));board.state['tracks'].update(left=track(net,[0,0],[1000000,0]),right=track(net,[1000000,0],[2000000,0]))
    board.state['tracks']['via']=dict(board.state['tracks']['left'],**{'class':'PCB_VIA','start':[1000000,0],'end':[1000000,0],'width':500000,'drill':200000,'via_type':4,'top':0,'bottom':2})
    cfg=dict(parts=[dict(ref=ref,region='controller' if ref!='LED7' else 'matrix',nets={'1':net}) for ref in ('U_MCU','C_CTRL','LED7')],plane_nets=['return-plane'])
    required=['controller-pad','controller-load']
    proof=dict(accepted=True,added_ids=['left','right','via'],required=2,required_pad_ids=required,missing={},missing_checked=True,source_copper_unchanged=True)
    def graph(actual,nets,deadline,**kwargs):
        objects=[(u,q) for u,q in actual.state['tracks'].items() if q['net'] in nets]
        objects += [(d['uuid'],dict(d,start=d['position'],end=d['position'])) for f in actual.state['footprints'].values() for d in f['pads'] if d['net'] in nets]
        nodes=[];parent=list(range(len(objects)))
        def find(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        for i,(uid,q) in enumerate(objects):
            points={tuple(q['start']),tuple(q['end'])};nodes.append(dict(item=NS(m_Uuid=NS(AsString=lambda uid=uid:uid)),net=q['net'],points=points))
            for j,other in enumerate(nodes[:-1]):
                if other['net']==q['net'] and other['points']&points:parent[find(i)]=find(j)
        groups=defaultdict(list)
        for i in range(len(nodes)):groups[find(i)].append(i)
        return dict(nodes=nodes,groups=dict(groups),find=find,nets=set(nets))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',NS(copper_graph=graph))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(snapshot=lambda b:deepcopy(b.state)))
    if local:
        from smilepcb.keyboard_routing import controller_plan as cp
        def members(actual,model):
            rows=defaultdict(dict);parts={p['ref']:p for p in model['parts']}
            for fp in actual.state['footprints'].values():
                for d in fp['pads']:rows[d['net']][d['uuid']]=dict(ref=fp['ref'],pad=deepcopy(d))
            return actual.state,parts,dict(rows)
        monkeypatch.setattr(cp,'_local_tools',lambda:NS(members=members,graph=graph))
        proof=dict(accepted=True,added_ids=['left','right','via'],pad_subsets=cp._local_subsets(board,cfg,{net}))
    return board,cfg,before,proof


def capture(api,context,kind):
    with generation_profile('dsn-process-v2'):
        return getattr(api,'verified_controller_local_ownership' if kind=='local' else 'verified_return_tree_ownership')(*context,deadline=time.monotonic()+2)


@pytest.mark.parametrize('kind',['returns','local'])
def test_only_actual_new_controller_copper_can_become_mutable(monkeypatch,kind):
    api=kernel();context=scene(monkeypatch,local=kind=='local');owned=capture(api,context,kind)
    assert set(owned.tracks)=={'left','right','via'}
    assert set(owned.pads)=={'controller-pad','controller-load'}
    assert not {'imported','seed'}&owned.tracks.keys()
    assert 'unrelated-pad' not in owned.pads
    with generation_profile('dsn-process-v2'):
        assert api.transient_access_ids(context[0].state,owned,{'row','column'})=={'left','right','via'}


@pytest.mark.parametrize('change',['rejected','old-id','omitted-new','changed-old','changed-footprint','wrong-net','bad-required','missing-alias','unclosed','model-mismatch'])
def test_return_creator_rejects_invalid_or_unclosed_binding(monkeypatch,change):
    api=kernel();context=scene(monkeypatch);board,cfg,before,proof=context
    if change=='rejected':proof['accepted']=False
    elif change=='old-id':proof['added_ids'].append('imported')
    elif change=='omitted-new':proof['added_ids'].remove('right')
    elif change=='changed-old':board.state['tracks']['seed']['width']=300000
    elif change=='changed-footprint':board.state['footprints']['chip']['pads'][0]['number']='2'
    elif change=='wrong-net':board.state['tracks']['left']['net']='another-net'
    elif change=='bad-required':proof['required']=3
    elif change=='missing-alias':proof['required_pad_ids'].remove('controller-load')
    elif change=='unclosed':board.state['tracks']['right']['start']=[1500000,0]
    else:cfg['parts'][1]['nets']['1']='wrong-model-net'
    with pytest.raises(ValueError):capture(api,context,'returns')


@pytest.mark.parametrize('kind',['returns','local'])
def test_locked_generated_piece_stays_hard(monkeypatch,kind):
    api=kernel();context=scene(monkeypatch,local=kind=='local');context[0].state['tracks']['via']['locked']=True
    owned=capture(api,context,kind);assert set(owned.tracks)=={'left','right'}


def test_no_new_copper_does_not_authorize_existing_returns(monkeypatch):
    api=kernel();context=scene(monkeypatch);board,cfg,before,proof=context
    before.update(deepcopy(board.state));proof['added_ids']=[]
    owned=capture(api,context,'returns');assert owned.tracks=={}


def test_combining_owners_rechecks_shape_and_never_releases_matrix_or_unknown(monkeypatch):
    api=kernel();context=scene(monkeypatch);owned=capture(api,context,'returns');state=context[0].state
    with generation_profile('dsn-process-v2'):
        merged=api.merge_provisional_ownership(state,{'row','column'},owned,None)
        assert set(merged.tracks)==set(owned.tracks)
        with pytest.raises(ValueError):api.merge_provisional_ownership(state,{'return-plane'},owned)
        state['tracks']['left']['width']+=1
        with pytest.raises(ValueError):api.merge_provisional_ownership(state,{'row','column'},owned)


def test_unified_handoff_captures_local_creator_at_the_actual_boundary(monkeypatch):
    from smilepcb.keyboard_routing import controller_plan as cp
    from test_controller_plan import scene as source_scene
    board,cfg,_,_=source_scene(monkeypatch);cfg['plane_nets']=['earth'];board.GetDesignSettings=lambda:NS(m_MinClearance=173000)
    source=dict(accepted=True,ports=[{'net':'ROW'},{'net':'CTRL'}],added_ids=[])
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',NS(route_controller_ports=lambda *a,**kw:source))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=lambda c:{'ROW','CTRL'}))
    handoff=NS();batch=NS(ports=source['ports'],fixed_ports=[{'net':'CTRL'}],seal='test-boundary',handoff=lambda:handoff)
    monkeypatch.setattr(cp,'certify_source_batch',lambda *a,**kw:batch)
    before_local=deepcopy(board.state);proof={};owner=object();calls=[]
    def close(actual,model,*a,**kw):
        actual.state['tracks']['actual-new-control']=dict(net='CTRL',locked=False)
        proof.update(accepted=True,added_ids=['actual-new-control'],local_closed={'nets':{'CTRL':{}}},external_obligations={})
        return proof
    def capture_owner(actual,model,before,produced,*,deadline):
        assert actual is board and model is cfg and before==before_local and produced is proof
        assert set(board.state['tracks'])-set(before['tracks'])==set(produced['added_ids'])
        calls.append(True);return owner
    monkeypatch.setattr(cp,'close_controller_obligations',close)
    monkeypatch.setattr(cp,'_controller_local_ownership',capture_owner,raising=False)
    got,fixed,result=cp.prepare_unified_sources(board,cfg,controller_refs={'U_MCU'},matrix_nets={'ROW'},local_closed=None,deadline=time.monotonic()+100)
    assert calls==[True] and got is handoff and got.controller_ownership is owner
    assert result['accepted'] and fixed['ports']==[]
