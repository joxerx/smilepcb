"""Compose must preserve historical interface evidence, not recapture heads."""
from copy import deepcopy
import ast
import json
from pathlib import Path
import time

import pytest

from test_post_matrix_interface_scope import receipt_scene
from smilepcb.keyboard_routing import interface_contract as api


def compose_scene(monkeypatch, *, closed=('row','column'), missing=('row','column')):
    _,board,cfg,receipt,state=receipt_scene(monkeypatch,closed=closed,missing=missing)
    for ref,fp in state['footprints'].items():fp['path']='/old/'+ref
    before=deepcopy(state)
    mapping={name:'/controller/'+name for name in ('row','column','control')}
    paths={f['ref']:'/new/'+f['ref'] for f in state['footprints'].values()}
    for fp in state['footprints'].values():
        fp['path']=paths[fp['ref']]
        for d in fp['pads']:d['net']=mapping.get(d['net'],d['net'])
    for t in state['tracks'].values():t['net']=mapping.get(t['net'],t['net'])
    for fp in board.GetFootprints():
        for d in fp.Pads():d.net=mapping[d.net]
    for part in cfg['parts']:
        part['nets']={number:mapping[net] for number,net in part['nets'].items()}
    import sys
    graph_api=sys.modules['smilepcb.keyboard_routing.native_graph']
    original=graph_api.copper_graph
    def graph(*args,**kwargs):
        g=original(*args,**kwargs)
        for node in g['nodes']:node['net']=mapping.get(node['net'],node['net'])
        g['nets']={mapping.get(n,n) for n in g['nets']}
        return g
    monkeypatch.setattr(graph_api,'copper_graph',graph)
    return board,cfg,receipt,before,state,mapping,paths


def test_rebind_keeps_absent_heads_and_reproves_current_closure(monkeypatch):
    board,cfg,receipt,before,after,mapping,paths=compose_scene(monkeypatch)
    original=deepcopy(receipt)
    rebound=api.rebind_interfaces(before,after,receipt,net_remap=mapping,paths=paths)
    assert receipt==original
    assert set(rebound['tracks'])==set(receipt['tracks'])
    for uid,shape in receipt['tracks'].items():
        assert rebound['tracks'][uid]=={**shape,'net':mapping[shape['net']]}
    proof=api.verify_interfaces(board,cfg,rebound,deadline=time.monotonic()+1,retire_completed=True)
    assert proof['owned_ids']==['old-control']
    assert set(map(tuple,proof['resolved']))=={('source-row',),('source-column',)}
    with pytest.raises(ValueError,match='copper changed'):
        api.verify_interfaces(board,cfg,rebound,deadline=time.monotonic()+1)


@pytest.mark.parametrize('mutation',['track','pad','frame','path','missing_path','extra_copper','receipt_pad','merge'])
def test_rebind_rejects_geometry_refresh_and_net_merges(monkeypatch,mutation):
    _,_,receipt,before,after,mapping,paths=compose_scene(monkeypatch)
    if mutation=='track':after['tracks']['old-control']['width']+=1
    elif mutation=='pad':after['footprints']['U_MCU']['pads'][0]['position']=[1,0]
    elif mutation=='frame':after['footprints']['U_MCU']['rotation']=90
    elif mutation=='path':after['footprints']['FIELD']['path']='/undeclared'
    elif mutation=='missing_path':paths.pop('FIELD')
    elif mutation=='extra_copper':after['tracks']['invented']=deepcopy(after['tracks']['old-control'])
    elif mutation=='receipt_pad':receipt['pads']['source-row']['position']=[1,0]
    else:
        mapping['column']=mapping['row']
        for fp in after['footprints'].values():
            for d in fp['pads']:
                if d['net']=='/controller/column':d['net']=mapping['row']
    with pytest.raises(ValueError):api.rebind_interfaces(before,after,receipt,net_remap=mapping,paths=paths)


def finalizer_publication():
    source=Path(__file__).resolve().parents[1]/'smilepcb/stm32/finalize_native.py'
    publication=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.With) and any(isinstance(q.context_expr,ast.Call) and isinstance(q.context_expr.func,ast.Name) and q.context_expr.func.id=='rollback_revision' for q in n.items))
    block=next(n for n in publication.body if isinstance(n,ast.If) and ast.unparse(n.test)=='contract is not None')
    return compile(ast.Module(body=[block],type_ignores=[]),str(source),'exec')


@pytest.mark.parametrize('scope,closed,missing,accepted',[
    ('matrix-with-controller',('row','column'),('row','column'),True),
    ('key-field',('row','column'),('row','column'),False),
    ('matrix-with-controller',('column',),('row','column'),False),
    ('matrix-with-controller',('row','column'),('row','column','control'),False),
])
def test_real_finalizer_only_hands_off_verified_closed_retirements(monkeypatch,tmp_path,scope,closed,missing,accepted):
    board,cfg,receipt,before,after,mapping,paths=compose_scene(monkeypatch,closed=closed,missing=missing)
    marker=tmp_path/api.MARKER;original=json.dumps(receipt);marker.write_text(original)
    published=[]
    env={'contract':{'scope':scope},'b':board,'cfg':cfg,'path':tmp_path/'keyboard.kicad_pcb',
         'before':before,'remap':mapping,'export_paths':paths,'time':time,'json':json,
         'snapshot':lambda b:deepcopy(after),'publish':lambda *a,**kw:published.append(True)}
    if accepted:
        exec(finalizer_publication(),env)
        result=json.loads(marker.read_text())
        assert published==[True] and set(result['tracks'])==set(receipt['tracks'])
    else:
        with pytest.raises(ValueError):exec(finalizer_publication(),env)
        assert not published and marker.read_text()==original


def test_finalizer_rejects_changed_live_head_or_missing_ordered_receipt(monkeypatch,tmp_path):
    board,cfg,receipt,before,after,mapping,paths=compose_scene(monkeypatch)
    published=[]
    env={'contract':{'scope':'matrix-with-controller'},'b':board,'cfg':cfg,'path':tmp_path/'keyboard.kicad_pcb',
         'before':before,'remap':mapping,'export_paths':paths,'time':time,'json':json,
         'snapshot':lambda b:deepcopy(after),'publish':lambda *a,**kw:published.append(True)}
    marker=tmp_path/api.MARKER
    for case in ('missing','changed'):
        if case=='changed':
            receipt['tracks']['old-control']['width']+=1
            marker.write_text(json.dumps(receipt))
        with pytest.raises(ValueError):exec(finalizer_publication(),env)
        assert not published
