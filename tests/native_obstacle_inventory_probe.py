import sys,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.obstacle_inventory import inventory_windows,window_groups
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
ns={'__name__':'fixture','__file__':str(Path(__file__))};code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0];exec(compile(code,'inventory-fixture','exec'),ns)
b,z=ns['board'](True)
for zone in list(b.Zones()):b.Delete(zone)
fp=p.FOOTPRINT(b);fp.SetReference('controller');fp.SetPosition(p.VECTOR2I(1000000,5500000));b.Add(fp)
for number,net,y in [('1','first',5000000),('2','second',6000000)]:
    pad=p.PAD(fp);pad.SetNumber(number);pad.SetPosition(p.VECTOR2I(2000000,y));pad.SetSize(p.VECTOR2I(500000,500000));pad.SetShape(p.PAD_SHAPE_RECT);pad.SetAttribute(p.PAD_ATTRIB_SMD);layers=p.LSET();layers.AddLayer(p.F_Cu);pad.SetLayerSet(layers);pad.SetNet(b.FindNet(net));fp.Add(pad)
before=snapshot(b)
rows,report=inventory_windows(b,{'first','second'},mcu_ref='controller',bounds=[0,0,20000000,15000000],protected_ids=(),deadline=time.monotonic()+5)
groups=window_groups(rows)
(out/'result.json').write_text(json.dumps(dict(report=report,rows=rows,group_nets=[[q['net'] for q in g] for g in groups],unchanged=snapshot(b)==before))+'\n')
