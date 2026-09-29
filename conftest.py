import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path or sys.path[0] != str(ROOT_DIR):
    sys.path.insert(0, str(ROOT_DIR))
