import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import create_app  # noqa: E402
from app.cache import redirect_cache  # noqa: E402
from app.rate_limit import rate_limiter  # noqa: E402


@pytest.fixture()
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let sqlite create it fresh
    flask_app = create_app(db_path=path)
    flask_app.config.update(TESTING=True)
    yield flask_app
    if os.path.exists(path):
        os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for f in (wal, shm):
        if os.path.exists(f):
            os.remove(f)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_shared_state():
    """Rate limiter and redirect cache are process-global singletons; reset
    them between tests so tests don't leak state into each other."""
    redirect_cache.clear()
    rate_limiter.reset()
    yield
    redirect_cache.clear()
    rate_limiter.reset()
