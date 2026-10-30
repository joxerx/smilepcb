"""Generated NPTH proposals must also fit the conservative DSN boundary."""
import json,sys
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.generation import generation_profile

def vec(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def board(gap=.25,center=10,diameter=1.75,plated=False):
 b=p.BOARD();b.GetDesignSettings().m_TrackMinWidth=150000;b.GetDesignSettings().m_MinClearance=150000;b.GetDesignSettings().m_HoleClearance=p.FromMM(gap)
 for name in ('signal','other'):b.Add(p.NETINFO_ITEM(b,name))
 for a,z in (((0,0),(40,0)),((40,0),(40,30)),((40,30),(0,30)),((0,30),(0,0))):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(*a));edge.SetEnd(vec(*z));edge.SetWidth(50000);edge.SetLayer(p.Edge_Cuts);b.Add(edge)
 fp=p.FOOTPRINT(b);fp.SetReference('J1');b.Add(fp);d=p.PAD(fp);d.SetAttribute(p.PAD_ATTRIB_PTH if plated else p.PAD_ATTRIB_NPTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(vec(diameter,diameter));d.SetDrillSize(vec(diameter,diameter));d.SetPosition(vec(center,10));layers=p.LSET();layers.AddLayer(p.F_Cu);layers.AddLayer(p.B_Cu);d.SetLayerSet(layers);fp.Add(d)
 if plated:d.SetNet(b.FindNet('signal'))
 return b

def probe(profile,gap=.25,clearance=.252,center=10,diameter=1.75,plated=False):
 b=board(gap,center,diameter,plated);before=snapshot(b);x=center+diameter/2+.075+clearance
 shape=p.SHAPE_SEGMENT(vec(x,9),vec(x,11),150000)
 if plated:shape=p.SHAPE_SEGMENT(vec(center,9),vec(center,11),150000)
 with generation_profile(profile):allowed=LocalRouter(b,track_width_nm=150000).clear(shape,p.F_Cu,'signal')
 assert snapshot(b)==before
 return allowed
report={'native_gap_retained':probe('native'),'dsn_guard_band_rejected':not probe('dsn-safe-v1'),
        'dsn_clear_path_retained':probe('dsn-safe-v1',clearance=.275),
        'strict_native_hole_rule':not probe('native',gap=.35,clearance=.3),
        'strict_rule_across_buckets':not probe('native',gap=3.5,center=5,diameter=.2,clearance=3.125),
        'same_net_plated_join':probe('dsn-safe-v1',plated=True)}
Path(sys.argv[1]).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
