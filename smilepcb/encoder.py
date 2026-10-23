"""Transactional encoder workflow for copies of the retained synthetic 3x4."""
from copy import deepcopy
import difflib
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from . import encoder_pipeline as pipeline
from . import service
from .encoder_recipe import FOOTPRINT
from .firmware import read_firmware
from .lab_tools import rule_checks
from .routing_checks import require_clean_rules

CACHE = '.smilepcb-encoder'
STATE = '.smilepcb-encoder-state.json'
BLOCK = 'encoder-pec12r-3x4-v1'
PROFILE = json.loads(Path(__file__).with_name('encoder_fixture.json').read_text())
BASE = PROFILE['source_files_sha256']
NAME = PROFILE['name']
LIBRARY_FILE = 'SmilePCB_Lab.pretty/' + FOOTPRINT.split(':')[1] + '.kicad_mod'
NEW = ('BOM.csv', LIBRARY_FILE)
MODIFIED = (NAME + '.kicad_pcb', NAME + '.kicad_sch', 'fp-lib-table', 'qmk/keyboard.json', 'firmware-manifest.json')
AFTER = sorted((*BASE, *NEW))


def supports(project):
    return (Path(project) / (NAME + '.kicad_pcb')).exists()


def safe(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe encoder project path')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Project/cache symlinks are not supported')
    if not current.resolve().is_relative_to(root):
        raise ValueError('Encoder path escapes its project')
    return current


def manifest(root, names):
    return {name: service.sha(safe(root, name)) for name in names}


def root_of(project):
    root = Path(project).resolve(strict=True)
    if safe(root, '.smilepcb-lab').read_text().strip() != service.MARKER:
        raise ValueError('Only the explicit synthetic 3x4 profile is supported')
    if (root / 'sym-lib-table').exists():
        raise ValueError('Unowned symbol library tables are not supported')
    # Check every known input, including nested library/firmware paths, before
    # any native tool can follow a table or child-sheet reference.
    for name in (*BASE, *NEW, CACHE, STATE):
        safe(root, name)
    if not (root / STATE).exists():
        if any((root / name).exists() for name in NEW) or (root / 'SmilePCB_Lab.pretty').exists():
            raise ValueError('Unowned encoder output already exists')
        if manifest(root, BASE) != BASE:
            raise ValueError('Synthetic project changed or does not match the supported profile')
    else:
        state = json.loads(safe(root, STATE).read_text())
        plan = load_plan(root, state['plan'], require_candidate=False)
        if manifest(root, AFTER) != plan['after_files_sha256']:
            raise ValueError('Applied project changed; refusing to overwrite later work')
    return root


def input_hashes(project):
    root = Path(project)
    return manifest(root, AFTER if (root / STATE).exists() else BASE)


def load_plan(root, plan, *, require_candidate=True):
    if (not isinstance(plan, dict) or plan.get('block') != BLOCK or plan.get('schema') != 1
            or not re.fullmatch('[0-9a-f]{32}', str(plan.get('id', '')))):
        raise ValueError('Unsupported encoder plan')
    folder = safe(root, CACHE + '/' + plan['id'])
    stored = json.loads(safe(folder, 'plan.json').read_text())
    if stored != plan or stored.get('source_files_sha256') != BASE or set(stored.get('after_files_sha256', {})) != set(AFTER):
        raise ValueError('Encoder plan contents changed')
    before = safe(folder, 'before')
    candidate = safe(folder, 'candidate/project')
    if manifest(before, BASE) != BASE:
        raise ValueError('Original backup changed')
    if require_candidate and manifest(candidate, AFTER) != plan['after_files_sha256']:
        raise ValueError('Prepared candidate changed')
    return stored


def copy_files(source, destination, names):
    for name in names:
        target = safe(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe(source, name), target)


def plan_encoder(project):
    root = root_of(project)
    # The retained experiment is immutable, including when an installed wheel
    # is invoked outside the checkout. Work only on a separately named copy.
    if root.parts[-4:] == ('.lab', 'kle-ng-3x4', 'low-current-02', 'project'):
        raise ValueError('Create a separate copy of the retained synthetic input first')
    with service.lock(root), service.NATIVE_LOCK:
        if (root / STATE).exists():
            return load_plan(root, json.loads((root / STATE).read_text())['plan'], require_candidate=False)
        cache = safe(root, CACHE); cache.mkdir(exist_ok=True)
        identifier = uuid.uuid4().hex
        folder = cache / identifier; folder.mkdir()
        try:
            before = folder / 'before'; before.mkdir()
            copy_files(root, before, BASE)
            result = pipeline.run(root, folder / 'candidate', input_files=list(BASE), power_profile=PROFILE['power_profile'])
            candidate = folder / 'candidate/project'
            after = manifest(candidate, AFTER)
            for name in set(BASE) - set(MODIFIED):
                if after[name] != BASE[name]:
                    raise ValueError('Prepared encoder changed an unowned input: ' + name)
            if manifest(root, BASE) != BASE:
                raise ValueError('Project changed while preparing the encoder plan')
            changes = [{'path': name, 'action': 'add' if name not in BASE else 'modify',
                        'before_sha256': BASE.get(name), 'after_sha256': after[name]}
                       for name in sorted((*MODIFIED, *NEW))]
            diffs = {}
            for name in ('qmk/keyboard.json', 'firmware-manifest.json', 'fp-lib-table', 'BOM.csv'):
                previous = (before / name).read_text().splitlines(keepends=True) if name in BASE else []
                diffs[name] = ''.join(difflib.unified_diff(previous, (candidate / name).read_text().splitlines(keepends=True),
                                                         fromfile='before/' + name, tofile='after/' + name))
            preview = {'gpio': [pin['gpio'] for pin in result['gpio']], 'placement_mm': result['placement_mm'],
                       'retained_tracks': result['preservation']['preserved_segments'],
                       'retained_vias': result['preservation']['preserved_vias'],
                       'added_tracks': result['preservation']['added_segments'], 'added_vias': result['preservation']['added_vias'],
                       'added_trace_length_mm': result['preservation']['added_trace_length_mm'],
                       'pad_net_changes': result['pad_net_changes'], 'files': changes, 'text_diffs': diffs,
                       'native_erc_drc_clean': result['native_erc_drc_clean'], 'power_model': result['power_model'],
                       'firmware_built': False, 'hardware_tested': False, 'fabrication_ready': False,
                       'limitations': result['encoder']}
            plan = {'schema': 1, 'block': BLOCK, 'id': identifier, 'status': 'prepared',
                    'source_files_sha256': BASE, 'after_files_sha256': after, 'preview': preview}
            (folder / 'plan.json').write_text(service.encode(plan))
            return deepcopy(plan)
        except BaseException as error:
            shutil.rmtree(folder)
            if isinstance(error, Exception) and not isinstance(error, ValueError):
                raise ValueError('Encoder preparation failed: ' + str(error)) from error
            raise


def preview_plan(project, plan):
    root = Path(project).resolve(strict=True)
    plan = load_plan(root, plan, require_candidate=not (root / STATE).exists())
    result = deepcopy(plan['preview'])
    result['applied'] = (root / STATE).exists()
    result['source_matches'] = input_hashes(root) == (plan['after_files_sha256'] if result['applied'] else BASE)
    return result


def apply_encoder(project, plan):
    root = Path(project).resolve(strict=True)
    with service.lock(root):
        plan = load_plan(root, plan, require_candidate=not (root / STATE).exists())
        if (root / STATE).exists():
            state = json.loads(safe(root, STATE).read_text())
            if state['plan'] != plan:
                raise ValueError('Another encoder plan is applied')
            return {'changed': False, 'verification': verify_encoder(root)}
        if input_hashes(root) != BASE:
            raise ValueError('Project changed after planning; create a new plan')
        root_of(root)
        candidate = root / CACHE / plan['id'] / 'candidate/project'
        with tempfile.TemporaryDirectory(prefix='.smilepcb-encoder-stage-', dir=root) as temporary:
            staged = Path(temporary)
            names = [*MODIFIED, *NEW, STATE]
            copy_files(candidate, staged, (*MODIFIED, *NEW))
            (staged / STATE).write_text(service.encode({'schema': 1, 'plan': plan}))

            def verify_commit():
                if manifest(root, AFTER) != plan['after_files_sha256']:
                    raise ValueError('Committed encoder files do not match the reviewed candidate')

            service.commit_files(root, staged, names, verify=verify_commit)
        return {'changed': True, 'plan_id': plan['id'], 'verification': {
            'encoder_installed': True, 'native_erc_drc_clean': True, 'preserved_tracks': plan['preview']['retained_tracks'],
            'preserved_vias': plan['preview']['retained_vias'], 'firmware_built': False,
            'hardware_tested': False, 'fabrication_ready': False}}


def verify_encoder(project):
    root = root_of(project)
    if not (root / STATE).exists():
        raise ValueError('No encoder plan has been applied')
    plan = load_plan(root, json.loads((root / STATE).read_text())['plan'], require_candidate=False)
    read_firmware(root)
    with service.NATIVE_LOCK, tempfile.TemporaryDirectory(prefix='verify-', dir=root / CACHE) as temporary:
        checks = rule_checks(root, Path(temporary) / 'rules', NAME)
        try:
            require_clean_rules(checks)
        except AssertionError as error:
            raise ValueError('Native encoder verification failed: ' + str(error)) from error
    return {'encoder_installed': True, 'native_erc_drc_clean': True,
            'preserved_tracks': plan['preview']['retained_tracks'], 'preserved_vias': plan['preview']['retained_vias'],
            'schematic_pcb_pin_assignments_checked': 174, 'firmware_built': False,
            'hardware_tested': False, 'fabrication_ready': False}


def inspect_encoder(project):
    root = root_of(project)
    return {'board': service.native('inspect', root / (NAME + '.kicad_pcb')), 'hashes': input_hashes(root),
            'config': json.loads((root / 'qmk/keyboard.json').read_text()), 'rgb_installed': True,
            'encoder_installed': (root / STATE).exists()}


def rollback_encoder(project):
    root = Path(project).resolve(strict=True)
    with service.lock(root):
        if not safe(root, STATE).exists():
            root_of(root)
            return {'changed': False}
        root = root_of(root)  # Refuses later manual work before any deletion.
        plan = load_plan(root, json.loads((root / STATE).read_text())['plan'], require_candidate=False)
        before = root / CACHE / plan['id'] / 'before'
        with tempfile.TemporaryDirectory(prefix='.smilepcb-encoder-stage-', dir=root) as temporary:
            staged = Path(temporary)
            copy_files(before, staged, MODIFIED)

            def verify_commit():
                if manifest(root, BASE) != BASE or any((root / name).exists() for name in (*NEW, STATE)):
                    raise ValueError('Rollback did not restore the original files')

            service.commit_files(root, staged, [*MODIFIED, *NEW, STATE], delete_names=(*NEW, STATE), verify=verify_commit)
        try:
            (root / 'SmilePCB_Lab.pretty').rmdir()
        except OSError:
            pass  # Preserve unrelated user files if any were added to this directory.
        return {'changed': True, 'plan_id': plan['id'], 'original_files_restored': True}
