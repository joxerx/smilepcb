"""Requested source preference policy and honest fallback provenance only."""
from copy import deepcopy


def source_policy_arguments(cfg):
    """Requested preference is not a claim that final heads are all coherent."""
    requested=cfg.get('routing_preparation',{}).get('coherent_via_banks',False)
    if type(requested) is not bool:raise ValueError('Coherent via banks must be boolean')
    return {'coherent_via_banks':True} if requested else {}


def legacy_fallback_provenance(source,failed_preference):
    """Retain the failed preference and identify the actual fallback supplier."""
    result=dict(source)
    result['coherent_via_banks']=dict(requested=True,source_accepted=bool(source.get('accepted')),
        outcome='legacy_escape_fallback',supplier='route_controller_escapes',complete_coherence=False,
        coherence_scope='No coherent-profile claim for legacy fallback heads',
        failed_preference_receipt=deepcopy(failed_preference))
    return result
