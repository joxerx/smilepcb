"""Explicit external toolchain configuration for installed keyboard builds."""
import hashlib
import json
import os
from pathlib import Path
import sys

FIELDS = {'kicad_app','java','freerouting_jar','qmk_home','qmk_bin','arm_gcc','compiler_libraries','dfu_suffix'}
JAR_SHA256 = 'f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7'


def load_tools(filename):
    path=Path(filename)
    if not path.is_file() or path.stat().st_size>65536:
        raise ValueError('Toolchain configuration must be a bounded JSON file')
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or set(data)!=FIELDS:
        raise ValueError('Toolchain configuration requires: '+', '.join(sorted(FIELDS)))
    result={}
    for key,value in data.items():
        values=value if key=='compiler_libraries' else [value]
        if not isinstance(values,list) or any(not isinstance(v,str) or not v or '\n' in v or '\x00' in v for v in values):
            raise ValueError('Invalid toolchain path: '+key)
        paths=[(path.parent/Path(v)).resolve() for v in values]
        if any(not p.exists() for p in paths):raise ValueError('Missing toolchain dependency: '+key)
        if key in ('kicad_app','qmk_home','compiler_libraries'):
            if any(not p.is_dir() for p in paths):raise ValueError('Toolchain directory required: '+key)
        else:
            if any(not p.is_file() for p in paths):raise ValueError('Toolchain file required: '+key)
            if key!='freerouting_jar' and any(not os.access(p,os.X_OK) for p in paths):
                raise ValueError('Toolchain program is not executable: '+key)
        result[key]=[str(p) for p in paths] if key=='compiler_libraries' else str(paths[0])
    if hashlib.sha256(Path(result['freerouting_jar']).read_bytes()).hexdigest()!=JAR_SHA256:
        raise ValueError('Toolchain requires the pinned Freerouting 2.5.0 JAR')
    app=Path(result['kicad_app'])/'Contents'
    required=[app/'MacOS/kicad-cli',app/'Frameworks/Python.framework/Versions/Current/bin/python3',app/'SharedSupport']
    if not all(p.exists() for p in required):raise ValueError('Incomplete KiCad macOS application')
    if not (Path(result['qmk_home'])/'Makefile').is_file():raise ValueError('Toolchain QMK source tree is incomplete')
    return result


def configure_kicad(tools, workspace):
    app=Path(tools['kicad_app'])/'Contents'
    os.environ.update(SMILEPCB_KICAD_APP=tools['kicad_app'],
                      SMILEPCB_KICAD_SUPPORT=str(app/'SharedSupport'),
                      SMILEPCB_KICAD_CLI=str(app/'MacOS/kicad-cli'),
                      SMILEPCB_KICAD_PYTHON=str(app/'Frameworks/Python.framework/Versions/Current/bin/python3'),
                      KICAD_CONFIG_HOME=str(Path(workspace).resolve()/'native-config'),
                      XDG_CACHE_HOME=str(Path(workspace).resolve()/'cache'))
    if 'smilepcb.runtime' in sys.modules:
        runtime=sys.modules['smilepcb.runtime']
        if runtime.APP.resolve()!=Path(tools['kicad_app']).resolve():
            raise ValueError('Start a fresh build process to select a different KiCad runtime')
