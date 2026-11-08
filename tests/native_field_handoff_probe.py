"""Exercise real native copper through the worker's matrix handoff boundary."""
import json,runpy,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing import field_contract as fc
from smilepcb import keyboard_native as api
real_run=api._run
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
sys.argv=[sys.argv[0],str(out/'fixture')]
make=runpy.run_path(str(Path(__file__).with_name('native_field_construction_probe.py')))['make']
b,cfg=make();r=build_field(b,cfg,row_layer=p.F_Cu,column_layer=p.B_Cu,deadline=time.monotonic()+15)
contract=fc.capture(snapshot(b),r);path=out/'keyboard.kicad_pcb';assert p.SaveBoard(str(path),b)
(out/fc.MARKER).write_text(json.dumps(contract))
ids=fc.verify_native(p.LoadBoard(str(path)),contract)
original=path.read_bytes();uid=ids[0]
def damage(request):
    board=p.LoadBoard(request['board'])
    for t in list(board.GetTracks()):
        if t.m_Uuid.AsString()==uid:board.Delete(t)
    assert p.SaveBoard(request['board'],board)
    return {'accepted':True}
api._run=damage
try:
 api.run({'operation':'complete','board':str(path)});rejected=False
except ValueError:rejected=True
rolled_back=path.read_bytes()==original
# Reset independently, so a broken guard cannot contaminate the addition case.
path.write_bytes(original)
def add(request):
    assert set(ids)<=set(request.get('protected_ids',()))
    board=p.LoadBoard(request['board']);t=p.PCB_TRACK(board);t.SetStart(p.VECTOR2I(18000000,6000000));t.SetEnd(p.VECTOR2I(18000000,7000000));t.SetWidth(150000);t.SetLayer(p.B_Cu);t.SetNet(board.FindNet('anchor'));board.Add(t);assert p.SaveBoard(request['board'],board)
    return {'accepted':True}
api._run=add
try:
 result=api.run({'operation':'components','board':str(path)});addition=result.get('matrix_field_preserved') is True
except (AssertionError,ValueError):addition=False
# Native graph must detect a broken access branch despite unchanged spines.
board=p.LoadBoard(str(path));access={uid for uid,role in r['roles'].items() if role.endswith('_access')}
for t in list(board.GetTracks()):
 if t.m_Uuid.AsString() in access:board.Delete(t)
try:fc.verify_native(board,contract);broken_access_rejected=False
except ValueError:broken_access_rejected=True
actual={d.m_Uuid.AsString() for f in b.GetFootprints() for d in f.Pads() if d.GetNumber()}
report={'damaging_pass_rejected':rejected,'saved_board_rolled_back':rolled_back,
 'addition_preserves_field':addition,'broken_access_rejected':broken_access_rejected,
 'all_physical_aliases_recorded':set(r['terminals'])==actual}
# First construction must roll back even before any old receipt exists.
first=out/'first';first.mkdir();first_path=first/'keyboard.kicad_pcb';first_path.write_bytes(original)
for failure in ('post-save-proof','receipt-write'):
    def fail_first(request):
        first_path.write_bytes(b'candidate board bytes')
        if failure=='receipt-write':(first/fc.MARKER).write_text('{')
        raise OSError(failure)
    api._run=fail_first
    try:api.run({'operation':'matrix_field','board':str(first_path)})
    except OSError:pass
    report['rollback_'+failure]=first_path.read_bytes()==original and not (first/fc.MARKER).exists()
    first_path.write_bytes(original)
    (first/fc.MARKER).unlink(missing_ok=True)
# A physical wall on the preferred row face requires structural inline vias.
bridged,bridged_cfg=make()
for item in list(bridged.GetDrawings()):
    if item.GetLayer()==p.F_Cu:bridged.Delete(item)
wall=p.PCB_SHAPE(bridged);wall.SetShape(p.SHAPE_T_RECT);wall.SetStart(p.VECTOR2I(7000000,0));wall.SetEnd(p.VECTOR2I(13000000,20000000));wall.SetWidth(50000);wall.SetFilled(True);wall.SetLayer(p.F_Cu);bridged.Add(wall)
r2=build_field(bridged,bridged_cfg,row_layer=p.F_Cu,column_layer=p.B_Cu,deadline=time.monotonic()+15)
row_vias=[u for u,t in snapshot(bridged)['tracks'].items() if t['class']=='PCB_VIA' and t['net']=='row']
report['inline_row_bridges_are_structural']=r2['complete'] and len(row_vias)>=2 and all(r2['roles'].get(u)=='row_trunk' for u in row_vias)
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
