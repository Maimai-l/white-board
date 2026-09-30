"""见 :mod:`inksync.codec`。这里只是让 ``whiteboard.codec`` 指向同一个模块。"""

import sys

from inksync import codec as _module

sys.modules[__name__] = _module
