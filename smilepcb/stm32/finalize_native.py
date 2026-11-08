"""STM32 recipe support: finalize native. Native checks are required."""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from collections import defaultdict
request=json.load(sys.stdin);path=Path(request['board']);cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text());b=p.LoadBoard(str(path));before=snapshot(b);by_ref={part['ref']:part for part in cfg['parts']};nets={n.GetNetname():n for n in b.GetNetsByNetcode().values()}
from smilepcb.keyboard_routing.generated_provenance import inventory,rebind_after_compose as rebind_generated,rollback_revision
provenance_before=inventory(path)
remap={};export_paths={}
for f in b.GetFootprints():
 ref=f.GetReference();part=by_ref[ref];chain=p.KIID_PATH()
 for value in request['paths'][ref].split('/'):
  if value:chain.push_back(p.KIID(value))
 f.SetPath(chain);export_paths[ref]=chain.AsString()
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
b.BuildConnectivity()
from smilepcb.keyboard_routing.field_contract import load,rebind_after_compose,verify_native,publish
contract=load(path.parent)
if contract is not None:
 contract=rebind_after_compose(before,snapshot(b),contract,net_remap=remap,paths=export_paths)
 verify_native(b,contract,deadline=time.monotonic()+15)
after=snapshot(b)['tracks']
for uid,shape in before['tracks'].items():
 expected=dict(shape);expected['net']=remap.get(shape['net'],shape['net'])
 assert after.get(uid)==expected,('Symbol synchronization changed core copper',uid,shape,after.get(uid))
land=require_via_land_clearance(b);core={f.GetReference() for f in b.GetFootprints() if f.GetReference()=='U_MCU' or f.GetReference().startswith('C_CORE')};g=copper_graph(b,{'+3V3','GND','NRST'},time.monotonic()+15);members=defaultdict(lambda:defaultdict(set))
for i,node in enumerate(g['nodes']):
 item=node['item']
 if isinstance(item,p.PAD) and item.GetParentFootprint().GetReference() in core:members[node['net']][g['find'](i)].add(item.GetParentFootprint().GetReference()+'/'+item.GetNumber())
groups={name:[sorted(v) for v in rows.values()] for name,rows in members.items()};print(json.dumps({'copied_copper_objects':len(before['tracks']),'copper_unchanged':True,'via_land':land,'core_groups_without_planes':groups,'core_gap_count_without_planes':sum(max(0,len(rows)-1) for rows in groups.values())}))

# Publish only after geometry, model and native contact checks pass. Matrix
# receipt precedes the candidate PCB, so interruption fails closed. A caught
# publication/rebind failure restores the previous PCB and all policy receipts.
with rollback_revision(path):
 if contract is not None:
  from smilepcb.keyboard_routing.interface_contract import load_interfaces,rebind_interfaces,verify_interfaces,MARKER
  receipt=load_interfaces(path.parent)
  if receipt is None and cfg.get('routing_preparation',{}).get('mode') in ('ordered-banks-v1','ordered-banks-v2'):
   raise ValueError('Missing required ordered controller interface receipt')
  if receipt is not None:
   receipt=rebind_interfaces(before,snapshot(b),receipt,net_remap=remap,paths=export_paths)
   verify_interfaces(b,cfg,receipt,deadline=time.monotonic()+15,
                     retire_completed=contract.get('scope')=='matrix-with-controller')
   (path.parent/MARKER).write_text(json.dumps(receipt,indent=2)+'\n')
  publish(b,path,contract,deadline=time.monotonic()+15)
 elif not p.SaveBoard(str(path),b):raise ValueError('Cannot save composed board')

 rebind_generated(path,provenance_before,net_remap=remap)
