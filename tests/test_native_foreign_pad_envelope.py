"""Native PAD-only collision occupancy; no board/controller qualification."""
import json,math,os,re,subprocess,time
from pathlib import Path
import pytest
import sexpdata
from test_exchange_pad_envelope_math import imported_octagon,inside

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select pinned native interpreter')
    root=Path(__file__).resolve().parents[1];runtime=Path(os.environ.get('SMILEPCB_CONTROL_RUNTIME',str(root)));out=tmp_path_factory.mktemp('foreign-pad')
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(runtime),str(Path(sexpdata.__file__).parent)))}
    probe=Path(__file__).with_name('native_foreign_pad_envelope_probe.py')
    deadline=time.monotonic()+30
    for mode,cap in((['--prepare'],10),([],20)):
        run=subprocess.run([interpreter,str(probe),str(out),*mode],cwd=root,env=env,capture_output=True,text=True,timeout=min(cap,max(.001,deadline-time.monotonic())))
        (out/('prepare.log'if mode else'exercise.log')).write_text(run.stdout+run.stderr)
        assert run.returncode==0,run.stdout+run.stderr
    result=json.loads((out/'report.json').read_text())
    # Actual native export through the normal circle preparation function;
    # bound the pinned imported45-degree PAD octagon, not only its64vertices.
    from smilepcb.keyboard_exchange import polygonize_circles
    for face,row in result.items():
        text,proof=polygonize_circles((out/face/'raw.dsn').read_text());(out/face/'prepared-circle.dsn').write_text(text)
        matches=[]
        for match in re.finditer(r'\(polygon [FB]\.Cu 0 ([^()]+)\)',text):
            values=list(map(float,match[1].split()));points=list(zip(values[::2],values[1::2]))
            if len(points)==64 and all(1640<math.hypot(x,y)<1660 for x,y in points):matches.append(points)
        assert matches,'Normal exported3300um PAD64gon missing'
        cx,cy=row['source_center_nm'];q=lambda value:math.floor(value*10+.5)*100
        octagons=[imported_octagon([(cx+q(x),cy-q(y))for x,y in points])for points in matches]
        row['normal_prepared_imported_tree_contained']=bool(row['envelope_points_nm'])and all(inside(row['envelope_points_nm'],point)for poly in octagons for point in poly)
    assert time.monotonic()<deadline,'Shared30s native+HOST proof cap exhausted'
    return result

@pytest.mark.parametrize('face',['front','back'])
@pytest.mark.parametrize('case',['foreign_native_net_binding_verified','fresh_rules_150_250','native_alias_and_opposite_face_via_contact','virtual_probe_not_electrically_joined','virtual_envelope_does_not_close_native_probe','native_native_witness_clear','dsn-safe-v1_native_witness_clear','v2_imported_foreign_rejected','same_net_uses_native_contacts','other_face_smd_remains_free','one_bucket_entry_per_uid','native_baseline_solder_control_clear','native_same_net_solder_land_retained','all_real_rotated_shape_outlines_contained','planner_refuses_unsafe_endpoint','positive_outward_route_allowed','original_copper_pads_rules_contacts_exact','actual_emitted_foreign_net_verified','saved_native_geometry_exact','native_solder_land_gate_passed','source_file_byte_exact','normal_prepared_imported_tree_contained'])
def test_real_native_pad_occupancy_and_original_contact_boundary(report,face,case):
    assert report[face][case]is True,report[face]
