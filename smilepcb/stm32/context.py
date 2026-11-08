"""Explicit run paths for isolated executable stages; never a checkout lookup."""
from contextlib import contextmanager
import os
from pathlib import Path
import runpy

WORKSPACE='SMILEPCB_STM32_WORKSPACE'
INPUTS='SMILEPCB_STM32_INPUTS'
# Private prototype selection; not a qualification or a public option.
STAGED_GENERATION_PROFILE='dsn-process-v2'


def workspace():
    value=os.environ.get(WORKSPACE)
    if not value:raise ValueError('STM32 stages require an explicit run workspace')
    return Path(value).resolve(strict=True)


def input_directory():
    value=os.environ.get(INPUTS)
    if not value:raise ValueError('STM32 stages require explicit layout inputs')
    return Path(value).resolve(strict=True)


@contextmanager
def run_context(output,inputs):
    updates={WORKSPACE:str(output),INPUTS:str(inputs)}
    previous={key:os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:yield
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def run_compatibility_script(name,checkout):
    """Keep old source commands as explicit adapters to the package engine."""
    if name=='generate.py':
        from .generate import main
        return main(compatibility_workspace=Path(checkout)/'.lab/public-controller')
    os.environ.setdefault(WORKSPACE,str(Path(checkout)/'.lab/public-controller'))
    # Old standalone completion commands select a supplied fixture by name.
    if name=='complete.py':
        import sys
        os.environ.setdefault(INPUTS,str(Path(__file__).parent/'inputs'/sys.argv[4]))
    runpy.run_path(str(Path(__file__).with_name(name)),run_name='__main__')


def install_cancellation_handler():
    """Let host-stage finally blocks reap separately grouped compiler children."""
    import signal
    def cancelled(signum,frame):
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise KeyboardInterrupt('STM32 generation cancelled')
    signal.signal(signal.SIGTERM,cancelled)


def resolve_completion_profile(requested,upstream):
    """Keep legacy completion native; new v2 must be explicit and consistent."""
    from smilepcb.keyboard_routing.generation import generation_profile
    selected='native' if requested is None else requested
    for name in (selected,upstream):
        if name is not None:
            with generation_profile(name):pass
    if (selected=='dsn-process-v2')!=(upstream=='dsn-process-v2'):
        raise ValueError('Completion generation profile differs from actual upstream provenance')
    return selected
