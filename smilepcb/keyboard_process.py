"""Explicit two-layer FR4 process rules; legacy behavior remains the default.

The compact via recipe keeps the 0.15 mm radial annulus and electrical rules.
Its 0.2 mm drill / 0.5 mm diameter is within published standard PCB capabilities.
This profile does not itself qualify a fabricated or assembled keyboard.
"""
LEGACY_VIAS='legacy-0p6-0p3'
COMPACT_VIAS='fr4-2l-1oz-0p5-0p2'

def process_rules(profile=None):
    if profile is None:profile=LEGACY_VIAS
    options={LEGACY_VIAS:(.6,.3),COMPACT_VIAS:(.5,.2)}
    if type(profile) is not str or profile not in options:
        raise ValueError('Unknown two-layer FR4 process profile')
    diameter,drill=options[profile]
    return {'min_clearance':.15,'min_copper_edge_clearance':.2,
            'min_track_width':.15,'min_via_annular_width':.15,
            'min_via_diameter':diameter,'min_through_hole_diameter':drill}

def project_settings(profile=None):
    rules=process_rules(profile)
    return {'meta':{'version':1,'filename':'keyboard.kicad_pro'},
            'board':{'design_settings':{'rules':rules}},
            'net_settings':{'classes':[{'name':'Default','clearance':rules['min_clearance'],
                'track_width':rules['min_track_width'],'via_diameter':rules['min_via_diameter'],
                'via_drill':rules['min_through_hole_diameter']}]}}
