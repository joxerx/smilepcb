"""Emit matching firmware and clean-build it in a disposable QMK workspace."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_hashes(folder):
    return {str(p.relative_to(folder)):digest(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def write_qmk(project, cfg):
    qmk = Path(project)/'qmk'
    (qmk/'keymaps/default').mkdir(parents=True,exist_ok=True)
    (qmk/'keyboard.json').write_text(json.dumps(cfg['qmk'],indent=2)+'\n')
    codes = ', '.join(k['keycode'] for k in cfg['layout']['keys'])
    (qmk/'keymaps/default/keymap.c').write_text('#include QMK_KEYBOARD_H\nconst uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {[0]=LAYOUT('+codes+')};\n')
    (qmk/'mcuconf.h').write_text('#pragma once\n#include_next <mcuconf.h>\n#undef STM32_HSI48_ENABLED\n#define STM32_HSI48_ENABLED TRUE\n')
    (qmk/'config.h').write_text('#pragma once\n#define WS2812_TRST_US 300\n')
    (qmk/'rules.mk').write_text('SRC += rgb_power.c\n' if cfg['layout']['rgb'] else '')
    if cfg['layout']['rgb']:
        shutil.copy2(Path(__file__).with_name('data')/'keyboard/rgb_power.c',qmk/'rgb_power.c')


def compile_qmk(project, output, tools):
    project,output = Path(project).resolve(),Path(output).resolve()
    source = Path(tools['qmk_home'])
    output.mkdir(exist_ok=False)
    runtime = output/'runtime'; runtime.mkdir()
    # Third-party sources are read through explicit links. Build products and
    # generated keyboard files remain entirely inside this new workspace.
    public_roots={'Makefile','paths.mk','LICENSE','license_GPLv2.md','license_GPLv3.md',
                  'license_Modified_BSD.md','requirements.txt','requirements-dev.txt','builddefs','data',
                  'drivers','lib','modules','platforms','quantum','tmk_core','util'}
    for path in source.iterdir():
        if path.name not in public_roots: continue
        (runtime/path.name).symlink_to(path.resolve(),target_is_directory=path.is_dir())
    (runtime/'users').mkdir(); (runtime/'layouts').mkdir()
    (runtime/'layouts/default').symlink_to((source/'layouts/default').resolve(),target_is_directory=True)
    target = runtime/'keyboards/smilepcb/generated'
    target.parent.mkdir(parents=True)
    shutil.copytree(project/'qmk',target)
    before = file_hashes(project/'qmk')
    bindir = output/'bin'; bindir.mkdir()
    compiler = str(Path(tools['arm_gcc']).resolve())
    libs = ':'.join(str(Path(p).resolve()) for p in tools['compiler_libraries'])
    gcc = bindir/'arm-none-eabi-gcc'
    gcc.write_text(f'#!{sys.executable}\nimport os,sys\nos.environ["DYLD_LIBRARY_PATH"]={libs!r}\nos.execv({compiler!r},[{compiler!r},*sys.argv[1:]])\n')
    gcc.chmod(0o700)
    shutil.copy2(tools['dfu_suffix'],bindir/'dfu-suffix')
    ini = bindir/'qmk.ini'
    ini.write_text('[user]\nqmk_home = '+str(runtime)+'\noverlay_dir = '+str(output/'disabled-userspace')+'\n')
    command = [str(tools['qmk_bin']),'--config-file',str(ini),'compile','--clean','-j','2',
               '-kb','smilepcb/generated','-km','default']
    env = os.environ.copy()
    env.update(PATH=str(bindir)+':'+str(Path(tools['qmk_bin']).parent)+':'+str(Path(tools['arm_gcc']).parent)+':'+env.get('PATH',''),
               QMK_HOME=str(runtime),SKIP_GIT='yes',NOT_REPO='yes',PYTHONDONTWRITEBYTECODE='1',
               MAKEFLAGS='-j2',CMAKE_BUILD_PARALLEL_LEVEL='2')
    started = time.perf_counter()
    with (output/'build.log').open('w') as log:
        child = subprocess.Popen(command,cwd=runtime,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        timed_out = threading.Event()
        def terminate():
            timed_out.set()
            try: os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError: pass
        timer = threading.Timer(240,terminate); timer.start()
        killer = threading.Timer(245,lambda: os.killpg(child.pid,signal.SIGKILL) if child.poll() is None else None)
        killer.start()
        try:
            _,status,usage = os.wait4(child.pid,0)
            child.returncode = os.waitstatus_to_exitcode(status)
        except BaseException:
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(); raise
        finally:
            timer.cancel(); killer.cancel()
    if child.returncode or timed_out.is_set():
        raise RuntimeError('QMK clean build failed; see '+str(output/'build.log'))
    files = {}
    for suffix,path in [('bin',runtime/'smilepcb_generated_default.bin'),('elf',runtime/'.build/smilepcb_generated_default.elf')]:
        dest = output/('firmware.'+suffix); shutil.copy2(path,dest)
        if not dest.stat().st_size: raise ValueError('Empty firmware artifact')
        files[suffix] = {'file':dest.name,'sha256':digest(dest),'bytes':dest.stat().st_size}
    if file_hashes(project/'qmk') != before or file_hashes(target) != before:
        raise ValueError('Compiled firmware inputs differ from emitted QMK sources')
    # Fingerprint the actual sources rather than claiming an unverified commit.
    manifest = {}
    for name in ('Makefile','builddefs','quantum','platforms','drivers','tmk_core','lib'):
        path = source/name
        for entry in ([path] if path.is_file() else sorted(path.rglob('*'))):
            if entry.is_file() and '.git' not in entry.parts: manifest[str(entry.relative_to(source))] = digest(entry)
    (output/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    result = {'clean_build':True,'flashed':False,'exit_code':0,'files':files,'source_hashes':before,
              'runtime_manifest_sha256':digest(output/'runtime-manifest.json'),
              'runtime_commit_verified':False,'elapsed_s':time.perf_counter()-started,
              'peak_qmk_process_rss_bytes':usage.ru_maxrss*(1 if platform.system()=='Darwin' else 1024),
              'compiler_jobs':2}
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
