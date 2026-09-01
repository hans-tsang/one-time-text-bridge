"""Pytest configuration: set up an isolated environment and database
before importing the application, and reset state between tests.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="ottb-test-")
_db_path = os.path.join(_tmp_dir, "test.db")

os.environ.setdefault("ENVIRONMENT", "development")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ["TRUSTED_PROXY_IPS"] = "127.0.0.1,testclient"
os.environ["BASE_URL"] = "http://testserver"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"


@pytest.fixture(autouse=True)
def _clean_database():
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
