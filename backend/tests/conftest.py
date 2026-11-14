"""Test setup: keep the tests away from the real database files in backend/data."""
import os
import tempfile

os.environ["ACCIALERT_DATA_DIR"] = tempfile.mkdtemp(prefix="accialert_test_")
os.environ["ACCIALERT_ENROL_TURN"] = "off"     # the head turn at enrolment is tested in test_enrol_turn.py
os.environ["ACCIALERT_ADMIN_AUTH"] = "off"      # the admin password is tested in test_admin.py, which switches it on
