"""Bounded admission of owned assembled scenes, with no Native dependency."""
from copy import deepcopy
import json
import math
import time

_CHECKS = ('assembly', 'mechanics', 'local_pads', 'returns', 'usb', 'context', 'probe_cleanup')


def _pose(value):
    point, angle = value['origin_nm'], value['rotation_deg']
    if (not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(x) is not int for x in point) or
            type(angle) is not int or angle not in (0, 90, 180, 270)):
        raise ValueError('Integer origin and cardinal rotation required')
    return tuple(point), angle


def _roots(values):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError('Mandatory physical root inventory required')
    result, pads = [], set()
    for value in values:
        if (not isinstance(value, (list, tuple)) or not value or
                any(not isinstance(k, str) or not k for k in value) or list(value) != sorted(set(value)) or
                pads.intersection(value)):
            raise ValueError('Canonical disjoint physical root keys required')
        pads.update(value); result.append(tuple(value))
    return tuple(sorted(result))


def _manifest(scene, pose):
    if not isinstance(scene, dict) or _pose(scene['pose']) != _pose(pose):
        raise ValueError('Assembly returned another pose')
    expected = _roots(scene['expected_source_keys'])
    manifest = scene['native_source_manifest']
    mandatory = manifest['mandatory_pad_keys']
    if (not mandatory or any(not isinstance(k, str) or not k for k in mandatory) or len(set(mandatory)) != len(mandatory)):
        raise ValueError('Trusted mandatory pad inventory required')
    pairs = manifest['pads']
    mapping = {q['pad_key']: q['pad_id'] for q in pairs}
    if (len(mapping) != len(pairs) or set(mapping) != set(mandatory) or
            any(not isinstance(uid, str) or not uid for uid in mapping.values()) or
            len(set(mapping.values())) != len(mapping)):
        raise ValueError('Native pad manifest must be a complete bijection')
    roots = _roots([q['source_key'] for q in manifest['roots']])
    if roots != expected or {pad for key in roots for pad in key} != set(mandatory):
        raise ValueError('Manifest/expected root partition omits mandatory pads')
    for row in manifest['roots']:
        actual = row['pad_ids']
        if len(actual) != len(set(actual)) or set(actual) != {mapping[k] for k in row['source_key']}:
            raise ValueError('Physical root differs from actual Native pad membership')
    return expected


def _proof(value, expected):
    if not isinstance(value, dict) or value.get('schema') != 'native-core-local-v1':
        raise ValueError('Actual local Native proof schema required')
    json.dumps(value, allow_nan=False)
    if _roots(value['mandatory_source_keys']) != expected or _roots(value['proved_source_keys']) != expected:
        raise ValueError('Mandatory, expected and proved roots must all agree')
    checks = value['checks']
    if any(checks.get(name) is not True for name in _CHECKS):
        raise ValueError('Every literal hard admission check is required')
    metrics = [value[k] for k in ('worst_bank_deficit', 'worst_bank_slack_nm', 'via_count', 'length_nm')]
    if any(type(x) is not int for x in metrics) or any(metrics[i] < 0 for i in (0, 2, 3)):
        raise ValueError('Finite integer physical comparison metrics required')
    deficit, slack, vias, length = metrics
    if 'side_space_nm' in value or checks.get('four_side_metrics') is True:
        from .core_pose_geometry import _balanced_metrics
        if checks.get('four_side_metrics') is not True:
            raise ValueError('Assembled four-side metrics require literal validation check')
        return (0, *_balanced_metrics(value), deficit, -slack, vias, length)
    return (0, deficit, -slack, vias, length)


def select_core_pose(poses, *, assemble, evaluate, dispose, deadline, max_candidates=3):
    """Compare baseline and leaders using one fixed allowance per owned trial.

    The same isolated scene is assembled and evaluated. Only a valid incumbent
    and active scene are retained. Initial source-root obligations stay fixed
    even when actual local preparation joins their electrical components.
    """
    if type(max_candidates) is not int or not 1 <= max_candidates <= 3:
        raise ValueError('Native comparison cap must be 1..3 including baseline')
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('Finite caller deadline required')
    poses = list(poses)[:max_candidates]
    if not poses:
        raise ValueError('poses[0] must be the mandatory baseline')
    for p in poses:
        _pose(p); json.dumps(p, allow_nan=False)
    started = time.monotonic()
    allowance = max(0, deadline-started)/len(poses)
    incumbent, incumbent_score, incumbent_index = None, None, None
    baseline_eligible, attempts = False, []
    for index, pose in enumerate(poses):
        trial_start = time.monotonic()
        if trial_start >= deadline:
            break
        cutoff = min(deadline, trial_start+allowance)
        attempt = dict(pose=deepcopy(pose), eligible=False, comparison_key=None, deadline=cutoff)
        active, score = None, None
        try:
            active = assemble(deepcopy(pose), deadline=cutoff)
            if active is incumbent and incumbent is not None:
                active = None
                raise ValueError('Assembly reused the retained incumbent')
            if time.monotonic() >= cutoff:
                raise TimeoutError('Assembly exceeded its fixed trial allowance')
            expected = _manifest(active, pose)
            manifest_binding = json.dumps(active['native_source_manifest'], sort_keys=True, allow_nan=False)
            proof = evaluate(active, deadline=cutoff)
            if time.monotonic() >= cutoff:
                raise TimeoutError('Evaluation exceeded its fixed trial allowance')
            key = _proof(proof, expected)
            if (_manifest(active, pose) != expected or
                    json.dumps(active['native_source_manifest'], sort_keys=True, allow_nan=False) != manifest_binding):
                raise ValueError('Evaluation changed the initial Native source manifest')
            if time.monotonic() >= cutoff:
                raise TimeoutError('Proof validation exceeded its fixed trial allowance')
            if incumbent_score is not None and len(key) != len(incumbent_score):
                raise ValueError('Balanced and legacy comparison modes are not comparable')
            score = key
            attempt.update(eligible=True, comparison_key=list(score), checks=deepcopy(proof['checks']))
        except Exception as error:
            score = None
            attempt.update(reason=type(error).__name__+': '+str(error))
        if index == 0:
            baseline_eligible = attempt['eligible']
        if score is not None and (incumbent_score is None or score < incumbent_score):
            if incumbent is not None:
                try:
                    dispose(incumbent)
                except Exception:
                    dispose(active)
                    raise
            incumbent, incumbent_score, incumbent_index = active, score, index
            active = None
        if active is not None:
            dispose(active)
        attempt['elapsed_s'] = time.monotonic()-trial_start
        attempts.append(attempt)
    improved = incumbent is not None and incumbent_index != 0
    reason = ('no_eligible_selection' if incumbent is None else
              'candidate_admitted_baseline_unproved' if improved and not baseline_eligible else
              'candidate_strictly_improves_baseline' if improved else 'baseline_retained_no_improvement')
    return dict(selected_scene=incumbent, selected_pose=deepcopy(incumbent['pose']) if incumbent is not None else None,
                improved=improved, attempts=attempts, reason=reason)
