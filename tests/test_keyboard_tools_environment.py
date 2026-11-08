"""Host-installed pure dependencies are visible at the Native child boundary."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import sexpdata
from smilepcb.keyboard_tools import configure_kicad


def test_configured_child_finds_same_installed_pure_parser_and_keeps_caller_paths(tmp_path,monkeypatch):
    monkeypatch.delitem(sys.modules,'smilepcb.runtime',raising=False)
    caller=[str(tmp_path/'caller-one'),str(tmp_path/'caller-two')]
    monkeypatch.setenv('PYTHONPATH',os.pathsep.join(caller))
    for name in ('SMILEPCB_KICAD_APP','SMILEPCB_KICAD_SUPPORT','SMILEPCB_KICAD_CLI','SMILEPCB_KICAD_PYTHON','KICAD_CONFIG_HOME','XDG_CACHE_HOME'):
        monkeypatch.setenv(name,os.environ.get(name,''))
    tools={'kicad_app':str(tmp_path/'KiCad.app')}
    configure_kicad(tools,tmp_path/'output')
    entries=os.environ['PYTHONPATH'].split(os.pathsep)
    staged=tmp_path/'output/native-python/sexpdata.py'
    assert entries[-2:]==caller and str(staged.parent) in entries
    assert str(Path(sexpdata.__file__).resolve().parent) not in entries
    assert staged.read_bytes()==Path(sexpdata.__file__).read_bytes()
    manifest=json.loads((staged.parent/'manifest.json').read_text())
    assert manifest['source_sha256']==hashlib.sha256(staged.read_bytes()).hexdigest()
    first=os.environ['PYTHONPATH'];configure_kicad(tools,tmp_path/'output')
    monkeypatch.setattr(sexpdata,'__file__',str(staged))
    configure_kicad(tools,tmp_path/'output')
    assert os.environ['PYTHONPATH']==first
    # -S removes host site discovery: only the configured child boundary exposes
    # the parser. This is HOST Python, never the Native interpreter.
    run=subprocess.run([sys.executable,'-S','-c','import json,sexpdata; print(json.dumps(sexpdata.__file__))'],env=os.environ.copy(),capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert Path(json.loads(run.stdout)).resolve()==staged.resolve()
