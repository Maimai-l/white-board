"""inksync 自己的测试：只把 packages/inksync 放进导入路径，不依赖白板应用。"""

import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))
