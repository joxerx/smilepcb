"""Portable Native callback authority checks on a synthetic two-layer fixture."""
import json,sys,time
from collections import Counter
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.virtual_copper import VirtualCopper
from smilepcb.keyboard_routing.obstacle_bundles import _connect
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
fixture=Path(__file__).with_name('native_joint_bundle_probe.py');ns={'__name__':'fixture','__file__':str(fixture)}
exec(compile(fixture.read_text().split('result={}')[0],str(fixture),'exec'),ns)
report={}
for mode in ('legacy-default','callback-accepts','callback-vetoes'):
    board,_=ns['board']()
    for item in list(board.GetTracks()):board.Delete(item)
    for zone in list(board.Zones()):board.Delete(zone)
    before=snapshot(board);deadline=time.monotonic()+3
    world=VirtualCopper(board,compact=True,track_width_nm=150000,deadline=deadline);world.connection_failures=Counter();seen=[]
    def predicate(route):seen.append(route);return mode=='callback-accepts'
    kwargs={} if mode=='legacy-default' else dict(admit_continuation=predicate)
    okay=_connect(world,(2000000,5000000),(6000000,5000000),p.F_Cu,p.F_Cu,'first',[0,0,20000000,15000000],deadline,**kwargs)
    assert snapshot(board)==before
    if mode=='callback-vetoes':assert not okay and seen and not world.operations and world.connection_failures['continuation-self-contact']>0
    else:assert okay and world.operations
    if mode=='callback-accepts':assert seen
    report[mode]=dict(connected=okay,predicate_calls=len(seen),native_board_unchanged=True,emitted_operations=len(world.operations),failures=dict(world.connection_failures))
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
