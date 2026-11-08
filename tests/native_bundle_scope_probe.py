import json,sys,time
from pathlib import Path
from smilepcb.native_worker import snapshot
from smilepcb.keyboard_routing.bundle_polish import refine_bundles
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
# Reuse the existing real-copper fixture without its unrelated full scenario loop.
ns={'__name__':'fixture','__file__':str(Path(__file__))};code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0];exec(compile(code,'existing-native-joint-fixture','exec'),ns)
result={}
for case,focus in [('outside',[0,0,1000000,1000000]),('inside',[0,0,20000000,15000000])]:
 b,z=ns['board']();before=snapshot(b)
 try:r=refine_bundles(b,eligible_nets={'first','second'},protected_ids=(),deferred_zone_ids={z},deadline=time.monotonic()+15,focus_bounds=focus,preserve_v45_nets={'first'})
 except TypeError as e:r={'missing_api':str(e)}
 result[case]={'report':r,'unchanged':snapshot(b)==before}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
