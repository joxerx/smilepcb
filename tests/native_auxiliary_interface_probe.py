import json,sys,time
from pathlib import Path
from native_bank_fixture import make
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.interface_contract import capture_interfaces,MARKER
from smilepcb import keyboard_native as api
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);b,cfg,ports=make();b.Add(p.NETINFO_ITEM(b,'BOOT0'));b.Add(p.NETINFO_ITEM(b,'GND'));chip=next(f for f in b.GetFootprints() if f.GetReference()=='U_MCU');target=p.FOOTPRINT(b);target.SetReference('R_BOOT');b.Add(target)
for f,number,net,xy in ((chip,'6','BOOT0',(12000000,17000000)),(target,'1','BOOT0',(6000000,17000000)),(target,'2','GND',(6000000,18000000))):
 d=p.PAD(f);d.SetNumber(number);d.SetPosition(p.VECTOR2I(*xy));d.SetSize(p.VECTOR2I(1000000,300000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet(net));f.Add(d)
cfg['parts'][0]['nets']['6']='BOOT0';cfg['parts'].append({'ref':'R_BOOT','region':'controller','nets':{'1':'BOOT0','2':'GND'}});cfg['plane_nets']=['+3V3','GND'];cfg['routing_preparation']={'mode':'ordered-banks-v1'}
lookup={f.GetReference():f for f in b.GetFootprints()}
for q in cfg['parts']:q['footprint']=lookup[q['ref']].GetFPID().GetUniStringLibId()
receipt=capture_interfaces(b,cfg,ports,deadline=time.monotonic()+10);(out/MARKER).write_text(json.dumps(receipt));(out/'.smilepcb-keyboard.json').write_text(json.dumps(cfg));path=out/'keyboard.kicad_pcb';p.SaveBoard(str(path),b)
r=api.run({'operation':'controller_ports','board':str(path),'generation_profile':'dsn-safe-v1','budget_s':15});(out/'report.json').write_text(json.dumps(r,indent=2))
