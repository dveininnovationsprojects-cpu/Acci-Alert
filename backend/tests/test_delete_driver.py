"""Admin: delete an enrolled driver (face data, record, history and photos)."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin_auth  # noqa: E402
import app as server  # noqa: E402
import config  # noqa: E402


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """Two enrolled drivers (Aman, Bela) with photos, plus one old LBPH-only name (Old Ravi)."""
    monkeypatch.setattr(config, "DATASET_DIR", tmp_path / "dataset")
    monkeypatch.setattr(server.face_sface, "gallery", {"Aman": np.ones((5, 4)), "Bela": np.ones((7, 4))})
    saved = []
    monkeypatch.setattr(server.face_sface, "_save_gallery", lambda: saved.append(True))
    monkeypatch.setattr(server.face_recognizer, "labels_map", {0: "Old Ravi"})
    monkeypatch.setattr(server.face_recognizer, "labels_path", tmp_path / "labels.json")
    for name in ("Aman", "Bela"):
        folder = tmp_path / "dataset" / name
        folder.mkdir(parents=True)
        for i in range(3):
            (folder / f"{i:02d}.jpg").write_bytes(b"x")
        server.database.save_driver_enrolment(name, f"TN-{name}", 5, 5, 3, f"backend/dataset/{name}")
    monkeypatch.setattr(config, "ADMIN_AUTH_REQUIRED", False)
    server.state.active_driver_auth = 0
    server.state.active_driver_name = "Unknown"
    return server.app.test_client(), tmp_path, saved


def names(client):
    return [d["name"] for d in client.get("/api/enrolled").get_json()["drivers"]]


def test_delete_removes_face_data_record_history_and_photos(world):
    client, tmp, saved = world
    res = client.delete("/api/enrolled/Aman")
    body = res.get_json()
    assert res.status_code == 200 and body["status"] == "success" and body["face_samples"] == 5 and body["photos"] == 3
    assert "Aman" not in server.face_sface.gallery and saved                      # face data gone and saved
    assert not (tmp / "dataset" / "Aman").exists()                                  # photos gone
    assert "Aman" not in names(client)
    assert all(d["name"] != "Aman" for d in server.database.get_all_drivers())
    assert server.database.get_driver_history("Aman") == []
    events = server.database.get_events(limit=50, event_types=("DRIVER_DELETED",))["events"]
    assert events and events[0]["driver_name"] == "Aman" and events[0]["severity"] == "MEDIUM"


def test_other_drivers_are_untouched(world):
    client, tmp, _ = world
    client.delete("/api/enrolled/Aman")
    assert "Bela" in server.face_sface.gallery and (tmp / "dataset" / "Bela" / "00.jpg").exists()
    assert "Bela" in names(client)


def test_an_unknown_driver_gives_404_and_deletes_nothing(world):
    client, tmp, _ = world
    for name in ("Nobody", "..", ".hidden"):
        assert client.delete(f"/api/enrolled/{name}").status_code in (404, 308, 405)
    assert (tmp / "dataset" / "Aman").exists() and (tmp / "dataset" / "Bela").exists()


def test_photos_outside_the_photo_folder_are_never_deleted(world, monkeypatch, tmp_path):
    client, tmp, _ = world
    outside = tmp_path / "keep_me"
    outside.mkdir()
    (outside / "file.txt").write_text("x")
    monkeypatch.setattr(server.face_sface, "gallery", {"..": np.ones((1, 4))})
    client.delete("/api/enrolled/..")
    assert (outside / "file.txt").exists()


def test_a_driver_known_only_from_the_old_model_can_be_removed(world):
    client, _, _ = world
    assert "Old Ravi" in names(client)
    assert client.delete("/api/enrolled/Old Ravi").status_code == 200
    assert "Old Ravi" not in names(client)


def test_deleting_the_driver_who_unlocked_the_car_locks_it(world):
    client, _, _ = world
    server.state.active_driver_auth = 1
    server.state.active_driver_name = "Aman"
    client.delete("/api/enrolled/Aman")
    assert server.state.active_driver_auth == 0


def test_deleting_another_driver_leaves_the_car_unlocked(world):
    client, _, _ = world
    server.state.active_driver_auth = 1
    server.state.active_driver_name = "Bela"
    client.delete("/api/enrolled/Aman")
    assert server.state.active_driver_auth == 1


def test_a_deleted_driver_cannot_log_in_any_more(world, monkeypatch):
    client, _, _ = world
    client.delete("/api/enrolled/Aman")
    assert "Aman" not in server.face_sface.enrolled()


def test_delete_needs_the_admin_login(world, monkeypatch):
    client, tmp, _ = world
    monkeypatch.setattr(config, "ADMIN_AUTH_REQUIRED", True)
    admin_auth.reset_for_tests()
    res = client.delete("/api/enrolled/Aman")
    assert res.status_code == 401 and (tmp / "dataset" / "Aman").exists()
    assert client.post("/api/admin/login", json={"password": "1234"}).status_code == 200
    assert client.delete("/api/enrolled/Aman").status_code == 200


def test_the_roster_has_a_delete_button_with_a_confirmation():
    js = (Path(server.FRONTEND_DIR) / "static" / "js" / "driver_auth.js").read_text(encoding="utf-8")
    assert js.count("data-delete-driver") >= 4 and "AppDialog.confirm" in js and 'method: "DELETE"' in js
