"""Measure a no-routing session roundtrip before a heavy routing workflow."""
from collections import Counter
import json,shutil,hashlib
from pathlib import Path


def _via_geometry(rows):
    return Counter((v['net'],tuple(v['start']),tuple(v['end']),v['width'],v['drill'],
                    v['via_type'],v['top'],v['bottom'])
                   for v in rows.values() if v['class']=='PCB_VIA')


def prove_roundtrip(project,output,session,protected_ids):
    from smilepcb.lab_tools import worker,rule_checks
    from smilepcb.routing_copper import routing_delta,dsn_wire_shadow,dsn_via_shadow
    from smilepcb.routing_exchange import canonical_dsn
    from smilepcb.keyboard_build import BRIDGE
    scripts=Path(__file__).parent;output=Path(output);output.mkdir()
    candidate=output/'project';shutil.copytree(project,candidate)
    source_board=Path(project)/'keyboard.kicad_pcb';board=candidate/'keyboard.kicad_pcb'
    source_hash=hashlib.sha256(source_board.read_bytes()).hexdigest()
    report={'accepted':False,'scope':'exchange boundary only; no routing or keyboard completion claim'}
    def call(script,tag,**request):
        result=worker(script,request,output/(tag+'.log'),timeout_s=90)
        (output/(tag+'.json')).write_text(json.dumps(result,indent=2)+'\n');return result
    try:
        before=call(scripts/'inspect_exchange.py','before',board=str(source_board))
        imported=call(scripts/'import_session.py','import',board=str(board),ses=str(session),
                      protected_ids=protected_ids,replace_editable=True)
        after=call(scripts/'inspect_exchange.py','after',board=str(board))
        a,z=before['snapshot'],after['snapshot']
        if before['copper_layers']!=2 or after['copper_layers']!=2:raise ValueError('Roundtrip changed copper layers')
        if a['footprints']!=z['footprints']:raise ValueError('Roundtrip changed native components/pads')
        if before['pad_partitions']!=after['pad_partitions']:raise ValueError('Roundtrip changed actual pad connectivity')
        fixed=imported['protected_ids']
        if any(a['tracks'][uid]!=z['tracks'].get(uid) for uid in fixed):raise ValueError('Roundtrip changed fixed native copper')
        def sizes(rows):
            return Counter((v['net'],v['width'],v['drill'],v['via_type'],v['top'],v['bottom'])
                           for v in rows.values() if v['class']=='PCB_VIA')
        if sizes(a['tracks'])!=sizes(z['tracks']):raise ValueError('Roundtrip changed full native via sizes/types/counts')
        # Compare the actual native re-exports, never a guessed blanket tolerance.
        exported=[]
        for tag,path in (('before',source_board),('after',board)):
            call(BRIDGE,tag+'-export',operation='export_open',board=str(path),
                 dsn=str(output/(tag+'.dsn')),protected_ids=fixed)
            exported.append(canonical_dsn((output/(tag+'.dsn')).read_text()))
        shadows=[dsn_wire_shadow(text) for text in exported]
        coverage={'equivalent':False};report['exchange_trace_coverage']=coverage
        try:
            coverage['added']=len(routing_delta(shadows[0],shadows[1]))
            coverage['removed']=len(routing_delta(shadows[1],shadows[0]))
            coverage['equivalent']=not coverage['added'] and not coverage['removed']
        except ValueError as error:
            coverage['error']=str(error)
        if not coverage['equivalent']:raise ValueError('Roundtrip changed or could not prove exchange trace coverage')
        via_exports=[_via_geometry(dsn_via_shadow(text)) for text in exported]
        if via_exports[0]!=via_exports[1]:raise ValueError('Roundtrip changed exported via locations or geometry')
        editable={uid:v for uid,v in z['tracks'].items() if uid not in fixed}
        if _via_geometry(editable)-via_exports[0]:
            raise ValueError('Imported editable via location/geometry is absent from the actual source export')
        report['exchange_via_geometry']={'equivalent':True,
            'native_editable_geometry_matches_source_export':True,
            'native_positions_changed_by_actual_export':sum((_via_geometry(z['tracks'])-_via_geometry(a['tracks'])).values())}
        checks=rule_checks(candidate,output/'native-checks','keyboard',timeout_s=180)
        report['native_checks']=checks
        if checks['erc']['severity'] or checks['drc']['severity'].get('error') or checks['drc']['schematic_parity']:
            raise ValueError('Exchange-only roundtrip introduced native physical/ERC/parity errors')
        if hashlib.sha256(source_board.read_bytes()).hexdigest()!=source_hash:raise ValueError('Roundtrip changed saved source')
        report.update(accepted=True,native_components_and_pads_exact=True,native_pad_partition_exact=True,
                      fixed_copper_exact=True,protected_objects=len(fixed),
                      native_via_sizes_types_and_counts_exact=True,**{'import':imported})
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        report['source_unchanged']=hashlib.sha256(source_board.read_bytes()).hexdigest()==source_hash
        (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
