import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]  # agent modules, and the vendored lkfakes package
