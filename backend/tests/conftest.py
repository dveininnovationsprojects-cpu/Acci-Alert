"""Test setup: keep the tests away from the real database files in backend/data."""
import os
import tempfile

os.environ["ACCIALERT_DATA_DIR"] = tempfile.mkdtemp(prefix="accialert_test_")
