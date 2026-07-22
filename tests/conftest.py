import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anima.memory import MemoryStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "entity"))
    yield s
    s.close()
