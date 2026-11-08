"""Isolated actual-native controls for straight-projection barriers."""
import time
START=time.monotonic()
import argparse,json
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.field import axial_path,commit_plan
from smilepcb.keyboard_routing.native_graph import copper_graph


def mm(q):return tuple(round(x*1000000) for x in q)
def edge(board,a,z):
    item=p.PCB_SHAPE(board);item.SetShape(p.SHAPE_T_SEGMENT);item.SetStart(vec(mm(a)));item.SetEnd(vec(mm(z)));item.SetLayer(p.Edge_Cuts);item.SetWidth(50000);board.Add(item)
def loop(board,points):
    for a,z in zip(points,points[1:]+points[:1]):edge(board,a,z)
def pad(board,ref,position,net,*,size=.4,drill=None,front_only=True,npth=False):
    f=p.FOOTPRINT(board);f.SetReference(ref);board.Add(f)
    d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec(mm(position)));d.SetSize(vec(mm((size,size))));d.SetShape(p.PAD_SHAPE_CIRCLE)
    d.SetAttribute(p.PAD_ATTRIB_NPTH if npth else p.PAD_ATTRIB_PTH if drill else p.PAD_ATTRIB_SMD)
    if net:d.SetNet(board.FindNet(net))
    if drill:d.SetDrillSize(vec(mm((drill,drill))))
    layers=p.LSET()
    if front_only:layers.AddLayer(p.F_Cu)
    else:layers=p.LSET.AllCuMask()
    d.SetLayerSet(layers);f.Add(d);return d
class CountRouter(LocalRouter):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.via_queries=0
    def via_clear(self,q,net):self.via_queries+=1;return super().via_clear(q,net)

def run_case(name,stop):
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    for name_net in ('signal','foreign'):board.Add(p.NETINFO_ITEM(board,name_net))
    points=[(0,0),(20,0),(20,20),(0,20)]
    if name=='concave-outline':points=[(0,0),(6,0),(6,12),(8,12),(8,0),(20,0),(20,20),(0,20)]
    loop(board,points)
    if name=='edge-cut-slot':loop(board,[(9,8),(11,8),(11,12),(9,12)])
    y=10.46 if name=='annulus-only' else 10
    a,z=mm((2,y)),mm((18,y));left=pad(board,'FROM',(2,y),'signal');right=pad(board,'TO',(18,y),'signal')
    obstacle=None
    if name=='foreign-npth':obstacle=pad(board,'HOLE',(10,10),None,size=2,drill=2,front_only=False,npth=True)
    if name=='same-net-plated':obstacle=pad(board,'OWN',(10,10),'signal',size=1,drill=.4,front_only=False)
    if name=='annulus-only':
        # Native PTH with F.Cu-only land: the same UID's drill is clear,
        # while its front annulus blocks. B.Cu remains a real usable bridge.
        obstacle=pad(board,'LAND',(10,10),'foreign',size=.5,drill=.2,front_only=True)
    if name in ('opposite-face-bridge','same-net-plated'):
        wall=p.PCB_SHAPE(board);wall.SetShape(p.SHAPE_T_RECT);wall.SetStart(vec(mm((8,5))));wall.SetEnd(vec(mm((12,15))));wall.SetWidth(50000);wall.SetFilled(True);wall.SetLayer(p.F_Cu);board.Add(wall)
    before=snapshot(board);world=CountRouter(board,deadline=min(stop,time.monotonic()+4),compact=True,track_width_nm=150000)
    whole=p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width)
    predicates={}
    if name=='annulus-only':
        gap=max(world.minimum_clearance,world.net_clearances['signal'],world.net_clearances['foreign'])
        predicates={'front_copper_collision':p.SHAPE.Collide(whole,obstacle.GetEffectiveShape(p.F_Cu),gap),'drill_collision':p.SHAPE.Collide(whole,obstacle.GetEffectiveHoleShape(),world.hole_clearances[obstacle.m_Uuid.AsString()]),'front_land':obstacle.IsOnLayer(p.F_Cu),'back_land':obstacle.IsOnLayer(p.B_Cu)}
        assert predicates=={'front_copper_collision':True,'drill_collision':False,'front_land':True,'back_land':False},predicates
    t=time.monotonic();plan=axial_path(world,a,z,p.F_Cu,'signal');elapsed=time.monotonic()-t
    unchanged=snapshot(board)==before;assert unchanged
    negative=name in ('foreign-npth','edge-cut-slot','concave-outline')
    row={'case':name,'plan':plan,'via_clear_calls':world.via_queries,'planning_snapshot_unchanged':unchanged,'elapsed_s':elapsed,'native_predicates':predicates}
    if negative:row['passed']=plan is None and world.via_queries==0
    else:
        row['passed']=plan is not None
        if plan is not None:
            assert plan['paths'][0][0][0]==a and plan['paths'][-1][0][-1]==z
            row['passed'] &= len(plan['vias'])>=2 and any(side==p.B_Cu for path,side in plan['paths'])
            commit_plan(world,plan,'signal')
            after=snapshot(board);assert after['footprints']==before['footprints'] and all(after['tracks'].get(u)==v for u,v in before['tracks'].items())
            graph=copper_graph(board,{'signal'},stop,include_zones=False);ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
            assert graph['find'](ids[left.m_Uuid.AsString()])==graph['find'](ids[right.m_Uuid.AsString()])
            row['native_endpoint_graph_connected']=True;row['retained_exact']=True
    return row

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--case',required=True);args=ap.parse_args()
    select_generation_profile('dsn-process-v2')
    with using_quality_profile('turns'):row=run_case(args.case,START+10)
    args.output.write_text(json.dumps(row,indent=2,sort_keys=True)+'\n');print(json.dumps(row),flush=True)
if __name__=='__main__':main()
