# Unwanted files

Files moved out of the project because the running software does not use them. Nothing was deleted.
To put one back, move it to the same path without the "unwanted files" folder.

| File or folder | Why it is here |
|---|---|
| `backend/dataset_test/` | Test photos of 10 people (personal data). Only the face-accuracy tests used them, and those tests skip themselves when the photos are missing. |
| `backend/tools/camera_test.py` | A one-off camera check. Nothing calls it. |
| `database/` | SQL notes (`schema.sql`, `README.md`). The code creates its own tables, so this copy can go out of date. |
| `docs/T8_test_report.md`, `docs/accuracy_report.json` | Old accuracy results from the sample photos. |
| `docs/WEEKLY_TEST_REPORT_TEMPLATE.md` | A blank template. |

Not moved on purpose: `backend/dataset/` (the enrolled drivers' photos), `backend/data/` (databases and secrets), `backend/models/`, `backend/train_model.py`, `backend/tools/t8_acceptance.py` (a test imports it) and the other reports in `docs/`.
