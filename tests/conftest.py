import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# inksync 在 packages/inksync 里；白板应用导入时也会加进来，这里让单独用它的测试同样能导入
for path in (ROOT, ROOT / "packages" / "inksync"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
