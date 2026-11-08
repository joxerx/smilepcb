"""Native evidence for an exchange-only roundtrip; never changes a board."""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_native import validate
from smilepcb.keyboard_routing.completion import pad_partitions

from smilepcb.keyboard_routing.generation import select_generation_profile
request=json.load(sys.stdin);creation=select_generation_profile(request.get('generation_profile','native'));board=p.LoadBoard(request['board']);validate(board,request)
groups=sorted(sorted(group) for group in pad_partitions(board,time.monotonic()+40))
round_pads=[{'ref':f.GetReference(),'number':d.GetNumber(),'net':d.GetNetname(),'uuid':d.m_Uuid.AsString(),
             'position':[d.GetPosition().x,d.GetPosition().y],'size':[d.GetSize().x,d.GetSize().y],
             'drill':[d.GetDrillSize().x,d.GetDrillSize().y]}
            for f in board.GetFootprints() for d in f.Pads()
            if d.GetAttribute()==p.PAD_ATTRIB_PTH and d.GetShape()==p.PAD_SHAPE_CIRCLE
            and d.GetOffset().x==0 and d.GetOffset().y==0 and d.GetNumber() and d.GetNetname()]
print(json.dumps({'generation_profile':creation.name,'snapshot':snapshot(board),'pad_partitions':groups,
                 'copper_layers':board.GetCopperLayerCount(),'round_plated_pads':round_pads}))
