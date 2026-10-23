"""Native evidence for an exchange-only roundtrip; never changes a board."""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_native import validate
from smilepcb.keyboard_routing.completion import pad_partitions

request=json.load(sys.stdin);board=p.LoadBoard(request['board']);validate(board,request)
groups=sorted(sorted(group) for group in pad_partitions(board,time.monotonic()+40))
print(json.dumps({'snapshot':snapshot(board),'pad_partitions':groups,'copper_layers':board.GetCopperLayerCount()}))
