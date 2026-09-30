"""见 :mod:`inksync.netinfo`。这里只是让 ``whiteboard.netinfo`` 指向同一个模块。"""

import sys

from inksync import netinfo as _module

sys.modules[__name__] = _module
