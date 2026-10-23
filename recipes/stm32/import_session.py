"""Explicit additive or editable-replacement SES import for the native probe."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_native import validate
from smilepcb.keyboard_routing.session_import import apply_open_session

request=json.load(sys.stdin);path=Path(request['board']);board=p.LoadBoard(str(path));validate(board,request)
before=snapshot(board);replace=request.get('replace_editable',False)
if type(replace) is not bool:raise ValueError('Editable replacement mode must be boolean')
if not replace and set(before['tracks'])!=set(request['protected_ids']):
    raise ValueError('Additive probe requires every original conductor to be explicitly fixed')
result=apply_open_session(board,Path(request['ses']),request['protected_ids'])
if not p.SaveBoard(str(path),board):raise ValueError('Cannot save imported candidate')
saved=snapshot(p.LoadBoard(str(path)))
if saved['footprints']!=before['footprints'] or any(saved['tracks'].get(uid)!=before['tracks'][uid] for uid in result['protected_ids']):
    raise ValueError('Native save changed fixed source copper or footprints')
print(json.dumps(result))
