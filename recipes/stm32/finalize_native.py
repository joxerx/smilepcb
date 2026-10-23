"""STM32 recipe support: finalize native. Native checks are required."""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from collections import defaultdict
request=json.load(sys.stdin);path=Path(request['board']);cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text());b=p.LoadBoard(str(path));before=snapshot(b);by_ref={part['ref']:part for part in cfg['parts']};nets={n.GetNetname():n for n in b.GetNetsByNetcode().values()}
remap={}
for f in b.GetFootprints():
 ref=f.GetReference();part=by_ref[ref];chain=p.KIID_PATH()
 for value in request['paths'][ref].split('/'):
  if value:chain.push_back(p.KIID(value))
 f.SetPath(chain)
 f.SetField('Description','');f.GetField('Description').SetVisible(False)
 for name,value in request['fields'].get(ref,{}).items():f.SetField(name,value);f.GetField(name).SetVisible(False)
 for d in f.Pads():
  if not d.GetNumber():continue
  name=part['nets'][d.GetNumber()]
  old=d.GetNetname()
  if old in remap and remap[old]!=name:raise ValueError('Export split an existing pad net: '+old)
  remap[old]=name
  if name not in nets:nets[name]=p.NETINFO_ITEM(b,name);b.Add(nets[name])
  d.SetNet(nets[name])
for t in b.GetTracks():
 old=t.GetNetname();name=remap.get(old,old)
 if name not in nets:nets[name]=p.NETINFO_ITEM(b,name);b.Add(nets[name])
 t.SetNet(nets[name])
b.BuildConnectivity();p.SaveBoard(str(path),b);b=p.LoadBoard(str(path));after=snapshot(b)['tracks']
for uid,shape in before['tracks'].items():
 expected=dict(shape);expected['net']=remap.get(shape['net'],shape['net'])
 assert after.get(uid)==expected,('Symbol synchronization changed core copper',uid,shape,after.get(uid))
land=require_via_land_clearance(b);core={f.GetReference() for f in b.GetFootprints() if f.GetReference()=='U_MCU' or f.GetReference().startswith('C_CORE')};g=copper_graph(b,{'+3V3','GND','NRST'},time.monotonic()+15);members=defaultdict(lambda:defaultdict(set))
for i,node in enumerate(g['nodes']):
 item=node['item']
 if isinstance(item,p.PAD) and item.GetParentFootprint().GetReference() in core:members[node['net']][g['find'](i)].add(item.GetParentFootprint().GetReference()+'/'+item.GetNumber())
groups={name:[sorted(v) for v in rows.values()] for name,rows in members.items()};print(json.dumps({'copied_copper_objects':len(before['tracks']),'copper_unchanged':True,'via_land':land,'core_groups_without_planes':groups,'core_gap_count_without_planes':sum(max(0,len(rows)-1) for rows in groups.values())}))
