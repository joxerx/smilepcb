"""STM32 recipe support: compose. Native checks are required."""
import sys,json,csv,shutil
from pathlib import Path
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.keyboard_tools import load_tools,configure_kicad
from smilepcb.keyboard_process import project_settings
settings=project_settings(sys.argv[3] if len(sys.argv)>3 else None)
tools=load_tools(sys.argv[1]);base=ROOT/'.lab/public-controller';configure_kicad(tools,base)
from smilepcb import schematic as s
from smilepcb.keyboard_build import write_schematic
from smilepcb.lab_tools import worker,export_netlist,rule_checks
from smilepcb.keyboard_firmware import write_qmk
from smilepcb.service import encode
for out in sorted((base/sys.argv[2]).iterdir()):
 if not (out/'placement.json').exists():continue
 placement=json.loads((out/'placement.json').read_text())
 if placement['status']!='placed':continue
 project=out/'project';cfg=json.loads((project/'.smilepcb-keyboard.json').read_text())
 # A changed chain cut must move its intentional open output with the last LED.
 last='LED'+str(cfg['layout']['rgb_order'][-1])
 for part in cfg['parts']:
  if part['ref'].startswith('LED'):
   if part['ref']==last:part['no_connect']=['2']
   else:part.pop('no_connect',None)
 symbols={}
 for node in s.children(s.read(project/'KeyboardParts.kicad_sym'),'symbol'):
  node=deepcopy(node);node[1]='KeyboardParts:'+node[1];symbols[node[1]]=node
 write_schematic(project,cfg,symbols)
 pro=deepcopy(settings)
 (project/'keyboard.kicad_pro').write_text(encode(pro))
 pins,paths,fields=export_netlist(project,out/'netlist.xml','keyboard')
 for part in cfg['parts']:
  if not part['ref'].startswith('#'):part['nets']={number:name for (ref,number),name in pins.items() if ref==part['ref']}
 (project/'.smilepcb-keyboard.json').write_text(encode(cfg));write_qmk(project,cfg)
 shutil.copy2(base/'sources/le-chiffre/COPYING',project/'LICENSE-Le-Chiffre-CERN-OHL-W-2.0')
 shutil.copy2(base/'sources/le-chiffre/readme.md',project/'README-Le-Chiffre.md')
 provenance={'donor':json.loads((base/'sources/le-chiffre/manifest.json').read_text()),'recipe':cfg.get('controller_recipe',{}),'changes':'Selected MCU and local capacitors; whole-block transforms. Protected USB width adapted to 0.2 mm. Local copied copper is retained or released according to the normalization report, then routed and independently checked. Other controller support comes from the existing SmilePCB circuit. The public source board remains unchanged.'}
 (project/'controller-source.json').write_text(encode(provenance))
 with (project/'BOM.csv').open('w',newline='') as stream:
  writer=csv.writer(stream);writer.writerow(['Reference','Value','Footprint']);writer.writerows((part['ref'],part['value'],part['footprint']) for part in cfg['parts'] if not part['ref'].startswith('#'))
 result=worker(Path(__file__).with_name('finalize_native.py'),{'board':str(project/'keyboard.kicad_pcb'),'paths':paths,'fields':fields},out/'finish.log')
 (out/'core-verification.json').write_text(encode(result))
 checks=rule_checks(project,out/'pre-route-checks','keyboard',timeout_s=120)
 print(out.name,json.dumps({'core':result,'native':checks}),flush=True)
