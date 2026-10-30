"""Check broad phase against exact KiCad geometry at and around boundaries."""
import json,sys
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.controller_ports import _port_collides

def point(x,y):return p.VECTOR2I(x,y)
shapes=[p.SHAPE_CIRCLE(point(x,y),r) for x,y,r in [(0,0,250000),(700000,0,100000),(1000000,1000000,50000),(-900000,-400000,250000)]]
shapes += [p.SHAPE_SEGMENT(point(*a),point(*z),w) for a,z,w in [((-1000000,0),(1000000,0),150000),((200000,200000),(1000000,1000000),200000),((0,400000),(0,1200000),300000)]]
cache={};checked=0
for a in shapes:
 for b in shapes:
  for gap in (0,149999,150000,150001,200000,500000):
   assert _port_collides(a,b,gap,cache)==p.SHAPE.Collide(a,b,gap)
   checked+=1
# Reusing immutable candidate bounds must preserve exact contact decisions.
assert len(cache)==len(shapes)
Path(sys.argv[1]).write_text(json.dumps({'checked':checked,'native_equivalent':True})+'\n')
