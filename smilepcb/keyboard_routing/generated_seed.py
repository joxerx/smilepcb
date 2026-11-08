"""Internal provenance for rerouting only freshly generated recipe copper.

This is an integrity receipt, not a signature or a manufacturing certificate.
All files are checked again before the generated-copper exchange starts.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
from .generation import uses_exchange_geometry

FILES = ("keyboard.kicad_pcb", "keyboard.kicad_pro", "layout.kle.json", ".smilepcb-keyboard.json")
PROOFS = ("controller-ports.json", "key-data-access.json", "controller-return-tree.json")
MARKER = "generated-seed.json"


def _read(path, limit=64 * 1024 * 1024):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError("Generated seed file must be a readable regular file: " + str(path)) from exc
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid or oversized generated seed file: " + str(path))
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Oversized generated seed file: " + str(path))
    return value


def _inventory(project,generation_profile,proof_mode="stage-receipts"):
    if proof_mode not in ("stage-receipts","controller-field"):
        raise ValueError("Unknown generated seed proof mode")
    project = Path(project)
    result = {}
    for name in FILES:
        result["project/" + name] = hashlib.sha256(_read(project / name)).hexdigest()
    field=project/'.smilepcb-field.json'
    if generation_profile=='dsn-process-v2':
        contract=json.loads(_read(field,8*1024*1024))
        if contract.get('generation_profile')!=generation_profile or contract.get('scope')!='matrix-with-controller':
            raise ValueError('Seed field generation profile or full matrix scope differs')
    if field.exists() or field.is_symlink():
        result['project/.smilepcb-field.json']=hashlib.sha256(_read(field,8*1024*1024)).hexdigest()
    from .generated_provenance import MARKER as PROVENANCE,generated_ids
    provenance=project/PROVENANCE
    if provenance.exists() or provenance.is_symlink():
        generated_ids(project/'keyboard.kicad_pcb')
        result['project/'+PROVENANCE]=hashlib.sha256(_read(provenance,16*1024*1024)).hexdigest()
    model=json.loads(_read(project/'.smilepcb-keyboard.json'))
    preparation=model.get('routing_preparation',{})
    if (generation_profile=='dsn-process-v2' and (not isinstance(preparation,dict)
            or preparation.get('mode') not in ('ordered-banks-v1','ordered-banks-v2')
            or preparation.get('generation_profile')!=generation_profile)):
        raise ValueError('Seed model generation profile differs from accepted preparation')
    if model.get('routing_preparation',{}).get('mode') in ('ordered-banks-v1','ordered-banks-v2'):
        from .interface_contract import MARKER as INTERFACE
        result['project/'+INTERFACE]=hashlib.sha256(_read(project/INTERFACE,8*1024*1024)).hexdigest()
    proofs=('matrix-field.json',) if proof_mode=='controller-field' else PROOFS
    for name in proofs:
        data = _read(project.parent / name, 4 * 1024 * 1024)
        proof=json.loads(data)
        if generation_profile=='dsn-process-v2' and proof.get('generation_profile')!=generation_profile:
            raise ValueError("Seed proof generation profile differs: " + name)
        if proof.get("accepted") is not True:
            raise ValueError("Seed recipe proof is not accepted: " + name)
        if proof_mode=='controller-field' and (proof.get('scope')!='matrix-with-controller'
                or proof.get('controller_connected') is not True
                or proof.get('field_generation_profile')!=generation_profile):
            raise ValueError('Seed matrix field proof is not controller-connected with the requested profile')
        result[name] = hashlib.sha256(data).hexdigest()
    return result


def write_manifest(project, *, generation_profile, proof_mode="stage-receipts"):
    project = Path(project)
    if not uses_exchange_geometry(generation_profile):
        raise ValueError("Generated routing exchange requires the explicit DSN-safe creation profile")
    manifest = {"schema": 1, "scope": "fresh-generated-recipe-copper",
                "generation_profile": generation_profile, "files": _inventory(project,generation_profile,proof_mode),
                "proof_mode": proof_mode,
                "completed_keyboard": False, "hardware_tested": False}
    path = project.parent / MARKER
    if path.is_symlink():
        raise ValueError("Generated seed receipt must not be a symlink")
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def validate_manifest(project):
    project = Path(project)
    result = json.loads(_read(project.parent / MARKER, 65536))
    if (result.get("schema") != 1 or result.get("scope") != "fresh-generated-recipe-copper"
            or not uses_exchange_geometry(result.get("generation_profile"))
            or result.get("completed_keyboard") is not False
            or result.get("files") != _inventory(project,result["generation_profile"],result.get("proof_mode","stage-receipts"))):
        raise ValueError("Source is not the unchanged fresh generated recipe seed")
    return result
