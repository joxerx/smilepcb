"""Explicit additive or editable-replacement SES import for the native probe."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_native import validate
from smilepcb.keyboard_routing.session_import import apply_open_session

from smilepcb.keyboard_routing.generation import select_generation_profile
request=json.load(sys.stdin);creation=select_generation_profile(request.get('generation_profile','native'));path=Path(request['board']);board=p.LoadBoard(str(path));validate(board,request)
from smilepcb.keyboard_routing.field_contract import load,verify_native
contract=load(path.parent)
if contract is not None:
 request['protected_ids']=sorted(set(request['protected_ids'])|set(verify_native(board,contract)))
before=snapshot(board);replace=request.get('replace_editable',False)
if type(replace) is not bool:raise ValueError('Editable replacement mode must be boolean')
if not replace and set(before['tracks'])!=set(request['protected_ids']):
    raise ValueError('Additive probe requires every original conductor to be explicitly fixed')
result=apply_open_session(board,Path(request['ses']),request['protected_ids'])
if contract is not None:verify_native(board,contract)
if not p.SaveBoard(str(path),board):raise ValueError('Cannot save imported candidate')
saved=snapshot(p.LoadBoard(str(path)))
if saved['footprints']!=before['footprints'] or any(saved['tracks'].get(uid)!=before['tracks'][uid] for uid in result['protected_ids']):
    raise ValueError('Native save changed fixed source copper or footprints')
result['generation_profile']=creation.name
print(json.dumps(result))
