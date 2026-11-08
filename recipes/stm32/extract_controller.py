"""Compatibility entrypoint; implementation is installed with SmilePCB."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
if __name__=='__main__':
    from smilepcb.stm32.context import run_compatibility_script
    run_compatibility_script('extract_controller.py',ROOT)
