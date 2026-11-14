"""Message boxes are cards inside the page, never the browser's own confirm / alert windows."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as server  # noqa: E402

FRONT = Path(server.FRONTEND_DIR)
SCRIPTS = [p for p in list((FRONT / "static" / "js").glob("*.js")) + [FRONT / "zones.js"] if p.name != "dialog.js"]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_native_confirm_alert_or_prompt(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w.])(?:window\.)?(?:confirm|alert|prompt)\s*\(", text), path.name


@pytest.mark.parametrize("page", ["index.html", "dashboard.html", "zones.html"])
def test_pages_load_the_dialog_card(page):
    html = (FRONT / page).read_text(encoding="utf-8")
    assert "dialog.js" in html and "dialog.css" in html


def test_dangerous_actions_ask_in_a_red_card():
    expected = {"static/js/dashboard.js": "Send SOS alert?", "static/js/driver_auth.js": "Clear the audit trail?",
                "static/js/zones.js": "Delete danger zone?", "zones.js": "Delete danger zone?"}
    for name, title in expected.items():
        text = (FRONT / name).read_text(encoding="utf-8")
        assert "AppDialog.confirm" in text and title in text and "danger: true" in text, name


def test_dialog_supports_keyboard_and_cancel():
    js = (FRONT / "static" / "js" / "dialog.js").read_text(encoding="utf-8")
    assert "Escape" in js and "role=\"alertdialog\"" in js and "aria-modal" in js
