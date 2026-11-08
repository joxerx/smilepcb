"""Inspect built artifacts when SMILEPCB_DIST_DIR selects them; no CAD."""
import importlib.util
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(params=('wheel','sdist'))
def artifact(request):
    directory=os.environ.get('SMILEPCB_DIST_DIR')
    if not directory:pytest.skip('Set SMILEPCB_DIST_DIR to inspect actual built distributions')
    archive=next(Path(directory).glob('*.whl' if request.param=='wheel' else '*.tar.gz'))
    if request.param=='wheel':
        with zipfile.ZipFile(archive) as z:members={n:z.read(n) for n in z.namelist() if not n.endswith('/')}
    else:
        with tarfile.open(archive) as t:members={n.name.split('/',1)[1]:t.extractfile(n).read() for n in t if n.isfile()}
    return request.param,archive,members


def test_built_artifact_carries_the_real_coordinator_inputs_stages_and_notices(artifact):
    kind,archive,members=artifact
    for name in ('generate.py','place.py','complete.py','route.py','route_global.py','context.py','controller-manifest.json',
                 'inputs/st40-grid48/layout.kle.json','inputs/st40-grid48/keyboard.json','inputs/st40-grid48/SOURCES.md'):
        assert 'smilepcb/stm32/'+name in members,(archive.name,name)
    notice=members['smilepcb/stm32/inputs/st40-grid48/SOURCES.md']
    assert b'85a298d5c642bd3f0558771611119981649b09ad' in notice
    metadata=next(data for name,data in members.items() if name.endswith('/METADATA') or name=='PKG-INFO')
    assert b'CC-BY-SA-4.0' in metadata


def test_built_artifact_excludes_private_process_docs_and_personal_owner_headers(artifact):
    kind,archive,members=artifact
    assert not any('/superpowers/' in name or Path(name).name=='AGENTS.md' or '.git/' in name for name in members)
    if kind=='sdist':
        with tarfile.open(archive) as t:
            assert all(n.uid==n.gid==0 and n.uname==n.gname=='root' for n in t)


def test_source_archive_owner_normalization_preserves_file_bytes_and_modes(tmp_path):
    path=ROOT/'smilepcb_build_backend.py'
    spec=importlib.util.spec_from_file_location('release_backend',path);backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
    archive=tmp_path/'sample.tar.gz'
    import io
    with tarfile.open(archive,'w:gz') as t:
        item=tarfile.TarInfo('package/file.txt');item.size=3;item.mode=0o640;item.uid=501;item.gid=20;item.uname='personal';item.gname='staff'
        t.addfile(item,io.BytesIO(b'abc'))
    backend.normalize_source_archive(archive)
    with tarfile.open(archive) as t:
        item=t.getmember('package/file.txt')
        assert t.extractfile(item).read()==b'abc' and item.mode==0o640
        assert (item.uid,item.gid,item.uname,item.gname)==(0,0,'root','root')
