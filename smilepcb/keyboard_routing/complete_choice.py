"""Immutable selection among complete, independently proved copper candidates.

This module never declares a path physical or electrically complete. Native
planning must prove that first; eligibility is the existing whole-area guard.
"""
from copy import deepcopy
from collections import defaultdict
import hashlib
import json
import math
from numbers import Real
import time



def _geometry_key(operations):
    groups = defaultdict(list)
    other = set()
    for i, op in enumerate(operations):
        if op['kind'] == 'via':
            other.add(('via', op['net'], tuple(op['position']), op['width'], op['drill']))
        elif op['kind'] == 'track':
            path = op.get('path') or (op['start'], op['end'])
            for j, (a, z) in enumerate(zip(path, path[1:])):
                a, z = tuple(a), tuple(z)
                if a == z:
                    continue
                dx, dy = z[0] - a[0], z[1] - a[1]
                g = math.gcd(dx, dy)
                d = (dx // g, dy // g)
                if d[0] < 0 or not d[0] and d[1] < 0:
                    d = (-d[0], -d[1])
                c = -d[1] * a[0] + d[0] * a[1]
                lo, hi = sorted(d[0] * q[0] + d[1] * q[1] for q in (a, z))
                groups[op['net'], op['layer'], op['width'], d, c].append((lo, hi))
        else:
            raise ValueError('Unknown candidate copper operation')
    # Integer line coordinates also cover arbitrary-angle retained copper;
    # no division/rounding can manufacture a different split identity.
    for (net, layer, width, direction, c), intervals in groups.items():
        merged = []
        for lo, hi in sorted(intervals):
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
            else:
                merged.append((lo, hi))
        other.add(('track', net, layer, width, direction, c, tuple(merged)))
    if not other:
        raise ValueError('Complete candidate must contain nonzero copper')
    return hashlib.sha256(json.dumps(sorted(other), separators=(',', ':')).encode()).hexdigest()


class CompleteChoice:
    """Keep the highest-scoring eligible in-budget complete geometry.

    Equal scores within1nm keep discovery order. Diagnostic candidates include
    completed but quality-ineligible routes. Expired and duplicate proposals
    never manufacture additional alternatives or replace a valid incumbent.
    """
    def __init__(self, *, deadline, clock=time.monotonic):
        if isinstance(deadline, bool) or not isinstance(deadline, Real) or not math.isfinite(deadline):
            raise ValueError('Finite candidate deadline required')
        if not callable(clock):
            raise ValueError('Candidate clock must be callable')
        self.deadline, self.clock = deadline, clock
        self._candidates, self._by_key = [], {}
        self._best = None
        self.duplicates = self.late = 0

    def consider(self, operations, axis, *, score, eligible):
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score):
            raise ValueError('Candidate score must be finite and non-boolean')
        if type(eligible) is not bool:
            raise ValueError('Candidate eligibility must be boolean')
        if self.clock() >= self.deadline:
            self.late += 1
            return False
        key = _geometry_key(operations)
        if key in self._by_key:
            existing = self._candidates[self._by_key[key]]
            if abs(existing['score'] - score) > 1 or existing['eligible'] != eligible:
                raise ValueError('Same geometry changed objective or eligibility')
            self.duplicates += 1
            return False
        candidate = dict(index=len(self._candidates), geometry_sha256=key,
            score=float(score), eligible=eligible, operations=deepcopy(operations), axis=deepcopy(axis))
        if self.clock() >= self.deadline:
            self.late += 1
            return False
        self._by_key[key] = candidate['index']
        self._candidates.append(candidate)
        if eligible and (self._best is None or score > self._candidates[self._best]['score'] + 1):
            self._best = candidate['index']
            return True
        return False

    @property
    def best(self):
        return None if self._best is None else deepcopy(self._candidates[self._best])

    @property
    def candidates(self):
        return deepcopy(self._candidates)

    def receipt(self):
        return dict(policy='best-complete', completed_unique=len(self._candidates),
                    eligible_unique=sum(c['eligible'] for c in self._candidates),
                    duplicates=self.duplicates, late=self.late, selected_index=self._best,
                    candidates=self.candidates)
