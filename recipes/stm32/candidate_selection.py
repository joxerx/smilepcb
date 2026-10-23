"""Rank incomplete diagnostics without confusing them with board acceptance."""


def candidate_score(checks,graph,access):
    if (checks['erc']['exit_code'] or checks['erc']['severity'] or checks['drc']['severity'].get('error') or
            checks['drc']['schematic_parity'] or graph['copper_layers']!=2 or not graph['pin_nets_match']):
        return None
    if access['accepted']!=(not access['missing']):
        raise ValueError('Controller acceptance contradicts its native missing-net evidence')
    return len(access['missing']),graph['pad_group_gaps']



def probe_candidate(route,inspect):
    """A bounded tool failure rejects one candidate, not the remaining search."""
    import subprocess
    stage='route'
    try:
        route();stage='native-controller'
        return inspect()
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired,RuntimeError) as error:
        return {'eligible':False,'failure_stage':stage,'error':str(error)}
