import json,sys,time
from pathlib import Path
from native_bank_fixture import make
from smilepcb.native_worker import snapshot
from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
from smilepcb.keyboard_routing.generation import generation_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);results={}
for case in ('valid','expired','blocked','retry','usb-first','fixed-signals'):
 b,cfg,ports=make();cfg['plane_nets']=['+3V3'];owned={u for q in ports for u in q['added_ids']}
 for t in list(b.GetTracks()):
  if t.m_Uuid.AsString() in owned:b.Delete(t)
 if case=='blocked':
  from smilepcb.native_worker import p
  f=p.FOOTPRINT(b);f.SetReference('obstacle');b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(21100000,14500000));d.SetSize(p.VECTOR2I(100000,300000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('+3V3'));f.Add(d)
 if case=='usb-first':
  from smilepcb.native_worker import p
  chip=next(f for f in b.GetFootprints() if f.GetReference()=='U_MCU');target=p.FOOTPRINT(b);target.SetReference('J_UDB');b.Add(target)
  for i,name in enumerate(('USB_DP','USB_DM')):
   b.Add(p.NETINFO_ITEM(b,name))
   for fp,x,number in ((chip,12000000,str(i+6)),(target,5000000,str(i+1))):
    d=p.PAD(fp);d.SetNumber(number);d.SetPosition(p.VECTOR2I(x,14000000+i*500000));d.SetSize(p.VECTOR2I(1000000,300000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet(name));fp.Add(d)
   cfg['parts'][0]['nets'][str(i+6)]=name
  cfg['parts'].append({'ref':'J_UDB','region':'controller','nets':{'1':'USB_DP','2':'USB_DM'}})
 if case=='fixed-signals':
  from smilepcb.native_worker import p
  b.Add(p.NETINFO_ITEM(b,'enable'));chip=next(f for f in b.GetFootprints() if f.GetReference()=='U_MCU');target=p.FOOTPRINT(b);target.SetReference('R_CONTROL');b.Add(target)
  for fp,number,x in ((chip,'6',12000000),(target,'1',6000000)):
   d=p.PAD(fp);d.SetNumber(number);d.SetPosition(p.VECTOR2I(x,17000000));d.SetSize(p.VECTOR2I(1000000,300000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('enable'));fp.Add(d)
  cfg['parts'][0]['nets']['6']='enable';cfg['parts'].append({'ref':'R_CONTROL','region':'controller','nets':{'1':'enable'}})
 before=snapshot(b)
 import smilepcb.keyboard_routing.ordered_controller as module
 original=module.extend_controller_bundles;original_ports=module.route_controller_ports;calls=[];usb_first=[];fixed_before_probe=[];fixed_promises_before_probe=[];original_probe=module.probe_gpio_access
 if case=='usb-first':
  def checked_ports(*args,**kwargs):
   from smilepcb.keyboard_routing.native_graph import copper_graph
   g=copper_graph(b,{'USB_DP','USB_DM'},time.monotonic()+5);usb_first.append(all(len({g['find'](i) for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PAD) and n['net']==net})==1 for net in ('USB_DP','USB_DM')))
   return original_ports(*args,**kwargs)
  module.route_controller_ports=checked_ports
 if case=='fixed-signals':
  def checked_probe(*args,**kwargs):
   fixed_before_probe.append(any(t.GetNetname()=='enable' for t in b.GetTracks()))
   from smilepcb.keyboard_routing.interface_launches import current_launches
   fixed_promises_before_probe.append(any(q['net']=='enable' for q in current_launches()))
   return original_probe(*args,**kwargs)
  module.probe_gpio_access=checked_probe
 if case=='retry':
  def initial_failure(*args,**kwargs):
   calls.append(1)
   return {'accepted':False,'reason':'Injected group-only rejection after a native-valid first escape'} if len(calls)==1 else original(*args,**kwargs)
  module.extend_controller_bundles=initial_failure
 with generation_profile('dsn-safe-v1'):
  updated,report=prepare_ordered_controller(b,cfg,deadline=time.monotonic()+30 if case!='expired' else time.monotonic()-1)
 module.extend_controller_bundles=original;module.route_controller_ports=original_ports;module.probe_gpio_access=original_probe
 fixed_proof={}
 if case=='fixed-signals' and report['accepted']:
  from copy import deepcopy
  from smilepcb.keyboard_routing.interface_contract import verify_interfaces,load_verified_interfaces,MARKER
  receipt=json.loads(json.dumps(report['interface']));fixed_proof['roundtrip']=verify_interfaces(b,updated,receipt,deadline=time.monotonic()+5)['accepted']
  trial=deepcopy(receipt);port=next(q for q in trial['ports'] if q['net']=='enable');trial['ports'].remove(port)
  for uid in port['added_ids']:trial['tracks'].pop(uid)
  for uid in port['pad_ids']:trial['pads'].pop(uid)
  try:verify_interfaces(b,updated,trial,deadline=time.monotonic()+5)
  except ValueError:fixed_proof['omission_rejected']=True
  else:fixed_proof['omission_rejected']=False
  folder=out/'required-v2';folder.mkdir()
  try:load_verified_interfaces(b,updated,folder,deadline=time.monotonic()+5)
  except ValueError:fixed_proof['missing_rejected']=True
  else:fixed_proof['missing_rejected']=False
  (folder/MARKER).write_text('{}')
  try:load_verified_interfaces(b,updated,folder,deadline=time.monotonic()+5)
  except ValueError:fixed_proof['empty_rejected']=True
  else:fixed_proof['empty_rejected']=False
 results[case]={'fixed_promises_before_probe':bool(fixed_promises_before_probe) and all(fixed_promises_before_probe),'fixed_before_probe':bool(fixed_before_probe) and all(fixed_before_probe),'fixed_proof':fixed_proof,'reserved_signals':sorted(q['net'] for q in report.get('interface',{}).get('ports',[])),'usb_first':bool(usb_first) and all(usb_first),'new_tracks_exact':(set(snapshot(b)['tracks'])-set(before['tracks']))==(set(report.get('interface',{}).get('tracks',{}))|set(report.get('returns',{}).get('added_ids',[]))|set(report.get('usb',{}).get('added_ids',[]))) if report['accepted'] else snapshot(b)==before,'bundle_calls':len(calls),'report':report,'unchanged':snapshot(b)==before,'pins_shared':updated['layout']['gpio']==updated['qmk']['matrix_pins']}
(out/'report.json').write_text(json.dumps(results,indent=2))
