# TST-002：根目录 conftest.py，确保从根目录运行 pytest 时能导入 backend 模块
import sys
from pathlib import Path

_backend_dir = Path(__file__).parent / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
