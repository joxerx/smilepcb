"""Real KiCad fixture: a multilayer detour must become a simple column."""
import sys,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.visual_polish import _parts
try:from smilepcb.keyboard_routing.cross_layer_polish import rebuild_geometry,extract_spanning_chains
except ImportError:rebuild_geometry=extract_spanning_chains=None
M=1000000
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def fixture():
 b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;b.Add(p.NETINFO_ITEM(b,'violet'))
 for a,z in zip([(0,0),(30,0),(30,22),(0,22)],[(30,0),(30,22),(0,22),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);e.SetStart(p.VECTOR2I(*(x*M for x in a)));e.SetEnd(p.VECTOR2I(*(x*M for x in z)));b.Add(e)
 for i,q in enumerate(((5,2),(11,18))):
  fp=p.FOOTPRINT(b);fp.SetReference('T'+str(i));b.Add(fp);pad=p.PAD(fp);pad.SetNumber('1');pad.SetAttribute(p.PAD_ATTRIB_SMD);pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(p.VECTOR2I(800000,800000));pad.SetPosition(p.VECTOR2I(*(x*M for x in q)));ls=p.LSET();ls.AddLayer(p.B_Cu);pad.SetLayerSet(ls);pad.SetNet(b.FindNet('violet'));fp.Add(pad)
 for layer,path in [(p.B_Cu,[(5,2),(5,7),(8,7)]),(p.F_Cu,[(8,7),(8,12)]),(p.B_Cu,[(8,12),(11,12),(11,18)])]:
  for a,z in zip(path,path[1:]):
   t=p.PCB_TRACK(b);t.SetNet(b.FindNet('violet'));t.SetLayer(layer);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*(x*M for x in a)));t.SetEnd(p.VECTOR2I(*(x*M for x in z)));b.Add(t)
 for q in ((8,7),(8,12)):
  v=p.PCB_VIA(b);v.SetNet(b.FindNet('violet'));v.SetPosition(p.VECTOR2I(*(x*M for x in q)));v.SetWidth(500000);v.SetDrill(200000);v.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v)
 return b
result={}
for case in ('geometry','protected','expired','partial-fault','partial-expiry','quality-veto'):
 b=fixture();before=snapshot(b);parts=_parts(b,time.monotonic()+5);original=LocalRouter.index
 if case in ('partial-fault','partial-expiry','quality-veto'):
  def fault(self,item):
   if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() not in before['tracks']:raise (RuntimeError('Injected after Add') if case=='partial-fault' else TimeoutError('Injected after Add'))
   return original(self,item)
  LocalRouter.index=fault
 try:
  if rebuild_geometry is None:r={'missing_api':True};chains=[]
  else:
   protected=set(before['tracks']) if case=='protected' else set();chains=extract_spanning_chains(b,eligible_nets={'violet'},protected_ids=protected,deadline=time.monotonic()+8)
   r=rebuild_geometry(b,nets={'violet'},preferred_layer=p.F_Cu,protected_ids=protected,deadline=time.monotonic()+30 if case!='expired' else time.monotonic()-1,mode='columns',**({'quality_gate':lambda before,after:False} if case=='quality-veto' else {}))
 except Exception as e:r={'fault':type(e).__name__+': '+str(e)};chains=[]
 finally:LocalRouter.index=original
 after=snapshot(b);result[case]={'report':r,'unchanged':before==after,'footprints_exact':before['footprints']==after['footprints'],'contacts_exact':_parts(b,time.monotonic()+5)==parts,'horizontal_nm':sum(abs(t['start'][0]-t['end'][0]) for t in after['tracks'].values() if t['class']=='PCB_TRACK' and t['start'][1]==t['end'][1]),'chain_count':len(chains),'max_chain_objects':max((len(q['ids']) for q in chains),default=0)}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
