"""STM32 recipe support: check access. Native checks are required."""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p
from smilepcb.keyboard_native import validate,verify_usb_pair
from smilepcb.keyboard_routing.controller_access import _requirements,_missing
from smilepcb.keyboard_routing.safety import require_via_land_clearance
request=json.load(sys.stdin);path=Path(request['board']);board=p.LoadBoard(str(path));validate(board,request)
cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text())
refs={part['ref'] for part in cfg['parts'] if part['region']=='controller' and not part['ref'].startswith('#')}
requirements=_requirements(board,refs);missing=_missing(board,requirements,time.monotonic()+75)
require_via_land_clearance(board);lengths=verify_usb_pair(board)
print(json.dumps({'accepted':not missing,'missing':missing,'required_nets':len(requirements),'usb_lengths_mm':lengths,'copper_layers':board.GetCopperLayerCount()}))
