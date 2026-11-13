# AcciAlert - Weekly Test Report

Prepared by: Prasanth (Testing and QA)
Week no.: ____ (of 8)   Week dates: ____________ to ____________   Report date: ____________

Copy this file for each week, for example `WEEK_03_TEST_REPORT.md`, and fill it in.
Test IDs and pass criteria are in `TEST_PLAN.md`.

## 1. Summary

- Overall status (circle one): GREEN (on track) / YELLOW (at risk) / RED (blocked)
- Tests run this week: ____   Passed: ____   Failed: ____   Not run / blocked: ____
- Open defects: ____   Fixed this week: ____   Retested and closed: ____
- One-line summary: ______________________________________________

## 2. Test results this week

| ID | Test | Tester | Date | Result (PASS / FAIL / BLOCKED / NOT RUN) | Measured value | Defect ID | Notes |
|---|---|---|---|---|---|---|---|
| T1 | Motor control | | | | | | |
| T2 | Alcohol detection | | | | | | |
| T3 | Obstacle detection | | | | | | |
| T4 | Speed vs stopwatch | | | | | | |
| T5 | Accident detection | | | | | | |
| T6 | GPS fix and accuracy | | | | | | |
| T7 | SMS delivery | | | | | | |
| T8 | Face recognition | | | | | | |
| T9 | Serial link stability | | | | | | |
| T10 | Dashboard latency | | | | | | |
| T11 | Danger-zone ENTER/EXIT | | | | | | |
| T12 | Laptop disconnected | | | | | | |
| T13 | Full demo x 3 | | | | | | |

## 3. Cumulative status (all weeks)

| ID | Latest result | Last tested | Passed and recorded? (Y/N) |
|---|---|---|---|
| T1 | | | |
| T2 | | | |
| T3 | | | |
| T4 | | | |
| T5 | | | |
| T6 | | | |
| T7 | | | |
| T8 | | | |
| T9 | | | |
| T10 | | | |
| T11 | | | |
| T12 | | | |
| T13 | | | |

Project is done only when T1 to T13 are all passed and recorded.

## 4. Defects

| ID | Test | Description | Severity (low/med/high) | Owner | Opened | Status (open / fixed / retested) | Closed |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## 5. Automated unit tests

- Command: `python -m pytest backend/tests -q`
- Result: ____ passed, ____ failed
- Failing tests (if any): ______________________________

## 6. Regression check (before any demo this week)

- [ ] Not a demo week, regression not required
- [ ] Regression checklist in `TEST_PLAN.md` section D completed
- Regression result: PASS / FAIL    Date: ____________

## 7. Danger-zone summary (T11)

| Zone | ENTER (expect 10) | EXIT (expect 10) | Duplicates | Result |
|---|---|---|---|---|
| | | | | |

## 8. Risks and blockers

| Item | Impact | Who can unblock | Needed by |
|---|---|---|---|
| | | | |

## 9. Plan for next week

- Tests scheduled: ______________________________
- Dependencies needed from others: ______________________________
- Database backup taken this week: Yes / No   (`python backend/backup.py`)

## 10. Sign-off

Prepared by: Prasanth   Signature: ____________   Date: ____________
Reviewed by (team lead): ____________   Signature: ____________   Date: ____________
