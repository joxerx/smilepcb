"""A full native matrix handoff supersedes retired preparation copper."""
import hashlib,json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import vec
from smilepcb.keyboard_routing.interface_contract import capture_interfaces,MARKER as INTERFACES
from smilepcb.keyboard_routing.field_contract import capture,verify_native,MARKER as FIELD
from smilepcb import keyboard_native as api
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);b=p.BOARD();b.SetCopperLayerCount(2)
for name in ('ROW0','COL0'):b.Add(p.NETINFO_ITEM(b,name))
for a,z in (((0,0),(20000000,0)),((20000000,0),(20000000,20000000)),((20000000,20000000),(0,20000000)),((0,20000000),(0,0))):
 e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetWidth(50000);b.Add(e)
def footprint(ref):
 f=p.FOOTPRINT(b);f.SetReference(ref);f.SetFPID(p.LIB_ID('Test','Pad'));b.Add(f);return f
def pad(fp,number,net,at,face):
 d=p.PAD(fp);d.SetNumber(number);d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((500000,500000)));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetPosition(vec(at));fp.Add(d);return d
def track(net,a,z,face):
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet(net));t.SetStart(vec(a));t.SetEnd(vec(z));t.SetLayer(face);t.SetWidth(150000);b.Add(t);return t
chip=footprint('U_MCU');row=pad(chip,'1','ROW0',(5000000,5000000),p.F_Cu);col=pad(chip,'2','COL0',(5000000,10000000),p.F_Cu)
pad(footprint('SW1'),'1','ROW0',(12000000,5000000),p.F_Cu);pad(footprint('SW2'),'1','COL0',(6000000,17000000),p.B_Cu)
row_lead=track('ROW0',(5000000,5000000),(7000000,5000000),p.F_Cu);col_lead=track('COL0',(5000000,10000000),(6000000,10000000),p.F_Cu)
v=p.PCB_VIA(b);v.SetNet(b.FindNet('COL0'));v.SetPosition(vec((6000000,10000000)));v.SetWidth(600000);v.SetDrill(300000);v.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v)
ports=[{'net':'ROW0','pad_ids':[row.m_Uuid.AsString()],'added_ids':[row_lead.m_Uuid.AsString()],'terminal_id':row_lead.m_Uuid.AsString(),'kind':'same_layer','via_id':None,'via_nm':None,'exit_nm':(7000000,5000000),'heading_origin_nm':(5000000,5000000),'exit_layer':p.F_Cu}, {'net':'COL0','pad_ids':[col.m_Uuid.AsString()],'added_ids':[col_lead.m_Uuid.AsString(),v.m_Uuid.AsString()],'terminal_id':v.m_Uuid.AsString(),'kind':'via','via_id':v.m_Uuid.AsString(),'via_nm':(6000000,10000000),'exit_nm':(6000000,10000000),'heading_origin_nm':(5000000,10000000),'exit_layer':p.F_Cu}]
cfg={'parts':[{'ref':'U_MCU','region':'controller','footprint':'Test:Pad','nets':{'1':'ROW0','2':'COL0'}},{'ref':'SW1','region':'matrix','footprint':'Test:Pad','nets':{'1':'ROW0'}},{'ref':'SW2','region':'matrix','footprint':'Test:Pad','nets':{'1':'COL0'}}], 'layout':{'gpio':{'rows':['A0'],'cols':['B0']},'rgb':False},'mcu_gpio_pads':{'A0':'1','B0':'2'},'plane_nets':[],'routing_preparation':{'mode':'ordered-banks-v1'}}
receipt=capture_interfaces(b,cfg,ports,deadline=time.monotonic()+10);(out/INTERFACES).write_text(json.dumps(receipt));(out/'.smilepcb-keyboard.json').write_text(json.dumps(cfg))
retired=row_lead.m_Uuid.AsString();b.Delete(row_lead);track('ROW0',(5000000,5000000),(12000000,5000000),p.F_Cu);track('COL0',(6000000,10000000),(6000000,17000000),p.B_Cu)
state=snapshot(b);roles={uid:('row_trunk' if t['net']=='ROW0' else 'column_access') for uid,t in state['tracks'].items()};terminals={d.m_Uuid.AsString():['rows' if d.GetNetname()=='ROW0' else 'columns',d.GetNetname()] for f in b.GetFootprints() for d in f.Pads()}
contract=capture(state,{'complete':True,'scope':'matrix-with-controller','roles':roles,'owned_ids':list(roles),'terminals':terminals,'retired_source_ids':[retired]});(out/FIELD).write_text(json.dumps(contract));path=out/'keyboard.kicad_pcb';assert p.SaveBoard(str(path),b)
report={'native_handoff_valid':bool(verify_native(p.LoadBoard(str(path)),contract))};digest=hashlib.sha256(path.read_bytes()).hexdigest()
try:
 result=api.run({'operation':'field_verify','board':str(path)});report['worker_accepts_authorized_retirement']=result.get('accepted') is True
except ValueError as error:
 report['worker_accepts_authorized_retirement']=False;report['failure']=str(error)
invalid=json.loads(json.dumps(contract));invalid['terminals'].pop(row.m_Uuid.AsString());(out/FIELD).write_text(json.dumps(invalid))
try:api.run({'operation':'field_verify','board':str(path)})
except ValueError:report['invalid_handoff_rejected']=True
else:report['invalid_handoff_rejected']=False
report['board_exact']=hashlib.sha256(path.read_bytes()).hexdigest()==digest;(out/'report.json').write_text(json.dumps(report,indent=2))
