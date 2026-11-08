"""Compose real retired interfaces without routing or reconstructing their heads."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_routing.field_contract import MARKER as FIELD, verify_native
from smilepcb.keyboard_routing.interface_contract import MARKER as INTERFACES, verify_interfaces
from smilepcb.keyboard_routing.generated_provenance import MARKER as PROVENANCE, inventory, record_creation

ROOT = Path(__file__).resolve().parents[1]
POLICIES = (PROVENANCE, FIELD, INTERFACES)

# The real writer finishes first. This fault then tests the outer saved revision
# transaction, including changes already made to the PCB and policy receipts.
FAIL_AFTER_WRITE = r"""
import json, runpy, sys
from pathlib import Path
from smilepcb.keyboard_routing import field_contract, generated_provenance
script, mode, board_name, witness_name = sys.argv[1:]
board = Path(board_name)
files = [board, *(board.parent/name for name in
    ('.smilepcb-generated.json', '.smilepcb-field.json', '.smilepcb-interfaces.json'))]
before = {path: path.read_bytes() if path.exists() else None for path in files}
module, name = ((field_contract, 'publish') if mode == 'publish_failure'
                else (generated_provenance, 'rebind_after_compose'))
original = getattr(module, name)
def fail_after_write(*args, **kwargs):
    original(*args, **kwargs)
    changed = {path.name: (path.read_bytes() if path.exists() else None) != data
               for path, data in before.items()}
    Path(witness_name).write_text(json.dumps(changed))
    raise RuntimeError('injected compose publication failure: ' + mode)
setattr(module, name, fail_after_write)
runpy.run_path(script, run_name='__main__')
"""


def saved_revision(path):
    return {item.name: item.read_bytes() if item.exists() else None
            for item in [path, *(path.parent/name for name in POLICIES)]}


def run_case(base, folder, finalizer, case):
    shutil.copytree(base, folder)
    path = folder/'keyboard.kicad_pcb'
    cfg_path = folder/'.smilepcb-keyboard.json'
    cfg = json.loads(cfg_path.read_text())
    receipt = json.loads((folder/INTERFACES).read_text())
    field = json.loads((folder/FIELD).read_text())
    metadata = json.loads((folder/'fixture.json').read_text())
    if case in ('reopened_matrix', 'missing_live_head'):
        board = p.LoadBoard(str(path))
        uid = (metadata['replacements']['ROW0'] if case == 'reopened_matrix' else
               next(q['terminal_id'] for q in receipt['ports'] if q['net'] == 'control'))
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() == uid:
                board.Delete(item)
        assert p.SaveBoard(str(path), board)
        if case == 'reopened_matrix':
            # Keep the remaining copper receipt exact so rejection must reach
            # independent native all-terminal closure, not a missing shape.
            field['tracks'].pop(uid)
            field['roles'].pop(uid)
            (folder/FIELD).write_text(json.dumps(field))
    if case == 'missing_receipt':
        (folder/INTERFACES).unlink()
    record_creation(path, {}, creator='native-compose-fixture')
    before = snapshot(p.LoadBoard(str(path)))
    remap = {net: '/composed/'+net for part in cfg['parts'] for net in part['nets'].values()}
    for part in cfg['parts']:
        part['nets'] = {number: remap[net] for number, net in part['nets'].items()}
    cfg_path.write_text(json.dumps(cfg))
    paths = {part['ref']: '/'+str(uuid.uuid5(uuid.NAMESPACE_URL, 'compose-fixture/'+part['ref']))
             for part in cfg['parts']}
    request = {'board': str(path), 'paths': paths, 'fields': {}}
    original = saved_revision(path)
    witness = folder/'failure-witness.json'
    command = [sys.executable, '-B', str(finalizer)]
    if case in ('publish_failure', 'rebind_failure'):
        command = [sys.executable, '-B', '-c', FAIL_AFTER_WRITE, str(finalizer),
                   case, str(path), str(witness)]
    started = time.monotonic()
    process = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True,
                             cwd=ROOT, env=os.environ.copy(), timeout=20)
    (folder/'finalize.stdout').write_text(process.stdout)
    (folder/'finalize.stderr').write_text(process.stderr)
    result = {'returncode': process.returncode, 'elapsed_s': time.monotonic()-started,
              'error': process.stderr.strip().splitlines()[-1] if process.stderr.strip() else None}
    if case != 'valid':
        result.update(rejected=process.returncode != 0, saved_revision_exact=saved_revision(path) == original)
        if witness.exists():
            result['written_before_failure'] = json.loads(witness.read_text())
        return result
    result['accepted'] = process.returncode == 0
    if process.returncode:
        return result
    board = p.LoadBoard(str(path))
    after = snapshot(board)
    rebound = json.loads((folder/INTERFACES).read_text())
    field = json.loads((folder/FIELD).read_text())
    proof = verify_interfaces(board, cfg, rebound, deadline=time.monotonic()+5, retire_completed=True)
    protected = verify_native(board, field, deadline=time.monotonic()+5)
    expected_receipt = deepcopy(receipt)
    for port in expected_receipt['ports']:
        port['net'] = remap[port['net']]
    for shape in [*expected_receipt['tracks'].values(), *expected_receipt['pads'].values()]:
        shape['net'] = remap[shape['net']]
    historical = set(receipt['tracks'])-set(before['tracks'])
    matrix_heads = {uid for port in receipt['ports'] if port['net'] in ('ROW0', 'COL0')
                    for uid in port['added_ids']}
    control = next(port for port in receipt['ports'] if port['net'] == 'control')
    resolved = set(map(tuple, proof['resolved']))
    unresolved = [port['net'] for port in proof['ports'] if tuple(sorted(port['pad_ids'])) not in resolved]
    result.update(
        native_full_matrix=bool(protected),
        original_receipt_relabelled=rebound == expected_receipt,
        original_matrix_heads_absent=bool(matrix_heads) and matrix_heads <= historical and not matrix_heads & after['tracks'].keys(),
        historical_ids_preserved=historical <= rebound['tracks'].keys() and not historical & after['tracks'].keys(),
        live_control_exact=all(after['tracks'].get(uid) == {**before['tracks'][uid], 'net': remap['control']}
                               for uid in control['added_ids']),
        only_control_unresolved=unresolved == ['/composed/control'],
        paths_rebound=all(row['path'] == paths[row['ref']] for row in after['footprints'].values()),
        current_copper_relabelled=after['tracks'] == {uid: {**shape, 'net': remap[shape['net']]}
                                                   for uid, shape in before['tracks'].items()},
        generated_bindings_relabelled=all(entry['binding'] == inventory(path)[uid]
            for uid, entry in json.loads((folder/PROVENANCE).read_text())['generated'].items()))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--finalizer', type=Path, default=ROOT/'smilepcb/stm32/finalize_native.py')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    prepared = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('native_post_matrix_interface_scope_probe.py')),
                               str(out/'fixtures'), '--prepare'], cwd=ROOT, env=os.environ.copy(),
                              capture_output=True, text=True, timeout=20)
    assert prepared.returncode == 0, prepared.stdout+prepared.stderr
    report = {}
    for face in ('front', 'back'):
        report[face] = {case: run_case(out/'fixtures'/face, out/face/case, args.finalizer, case)
                       for case in ('valid', 'reopened_matrix', 'missing_live_head', 'missing_receipt',
                                    'publish_failure', 'rebind_failure')}
    report['elapsed_s'] = time.monotonic()-started
    (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    accepted = all(report[face]['valid']['accepted'] for face in ('front', 'back'))
    print(json.dumps({'accepted': accepted, 'elapsed_s': report['elapsed_s'], 'report': str(out/'report.json')}))
    return 0 if accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
