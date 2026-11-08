"""Standard setuptools builds with portable, nonpersonal source-archive owners."""
from pathlib import Path
import tarfile
import tempfile


def normalize_source_archive(path):
    path=Path(path);mode=path.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.source-',suffix='.tar.gz',delete=False) as f:
        temporary=Path(f.name)
    try:
        with tarfile.open(path) as source,tarfile.open(temporary,'w:gz') as target:
            for member in source:
                member.uid=member.gid=0;member.uname=member.gname='root'
                member.pax_headers={k:v for k,v in member.pax_headers.items() if k not in ('uid','gid','uname','gname','atime','ctime')}
                target.addfile(member,source.extractfile(member) if member.isfile() else None)
        temporary.chmod(mode);temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def _backend():
    import importlib
    return importlib.import_module('setuptools.build_meta')


def build_sdist(sdist_directory,config_settings=None):
    name=_backend().build_sdist(sdist_directory,config_settings)
    normalize_source_archive(Path(sdist_directory)/name)
    return name


def __getattr__(name):return getattr(_backend(),name)
