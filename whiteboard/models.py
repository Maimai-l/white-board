"""见 :mod:`inksync.models`。这里只是让 ``whiteboard.models`` 指向同一个模块。"""

import sys

from inksync import models as _module

sys.modules[__name__] = _module
