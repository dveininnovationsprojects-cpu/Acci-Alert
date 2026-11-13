# AcciAlert - Test Checklist (T1 to T13)

Owner: Prasanth. Source: Requirements Document section 13.
"Pass criteria" and "Tester" come from the document. "Procedure" and "Record" are suggested
steps and can be changed by the tester. Thresholds are starting values; owners finalise them by calibration.

How to use: run the test, fill in Result (PASS / FAIL), Date, Tester initials and Notes.
A FAIL gets a defect entry (section B). All T1-T13 must pass and be recorded for project sign-off.

## A. Test cases

| ID | Test | Procedure | Pass criteria | Tester | Record | Result | Date | Notes |
|---|---|---|---|---|---|---|---|---|
| T1 | Motor control: forward, back, turn, stop, PWM | Send serial test commands for each movement and 3 PWM levels. Run along a 1 m ruler. | Smooth speed change; straight line within 10 cm per metre | Navin / Prasanth | Deviation (cm) per run | | | |
| T2 | Alcohol: clean air vs alcohol swab, 10 trials | After 3-minute pre-heat, 10 clean-air readings, then 10 swab readings. | Detected in 10/10; no false alarm in 10 clean trials | Yasik | ADC values, threshold used | | | |
| T3 | Obstacle at 10/20/30/50 cm | Place a flat obstacle at each distance and drive toward it. | Stops under 20 cm; slows under 40 cm | Yasik | Behaviour at each distance | | | |
| T4 | Speed: compare with stopwatch over 1 m | Drive 1 m at 3 speeds; compare dashboard speed with distance / stopwatch time. | Error within 10% | Yasik / Prasanth | Measured vs shown speed | | | |
| T5 | Accident: drop/tilt 10 times, drive normally 10 min | 10 drops or tilts, then 10 minutes normal driving. | 10/10 detection; 0 false triggers | Nivash | Detections, false triggers | | | |
| T6 | GPS fix and accuracy outdoors | Cold start outdoors; note time to fix; compare position with a known point. | Fix in under 5 min; error under 5 m | Sidharraj | Time to fix, error (m) | | | |
| T7 | SMS delivery with location | Trigger accident 5 times; time delivery. | Received in under 30 s in 5/5 tests | Sidharraj | Delivery time each test | | | |
| T8 | Face recognition: 3 authorised + 5 unknown people | Each person faces the camera; note result and time. | >= 90% accept, 0 unknown accepted | Arsal | Accept rate, time per recognition | | | |
| T9 | Serial link stability 30 min | Run telemetry 30 min; unplug and replug USB once. | No dropped or garbled lines; auto-reconnect works | Selvamani / Muniyappan | Line count, errors | | | |
| T10 | Dashboard latency and alerts | Trigger each alert type; time event to banner. | Under 1 s from event to banner | Muniyappan | Latency per alert type | | | |
| T11 | Danger zone ENTER/EXIT x 10 crossings per zone | See section C. | Exactly 10 ENTER and 10 EXIT, correct limits applied | Prasanth | Event counts per zone | | | |
| T12 | Laptop disconnected during run | Start a run, unplug the laptop, trigger obstacle and impact. | Car remains safe; safety rules still work | Selvamani | Behaviour observed | | | |
| T13 | Full demo scenario x 3 | Run the 7-step demo (Requirements Document) 3 times in a row. | All 7 steps succeed in 3 consecutive runs | All | Pass/fail per step per run | | | |

## B. Defect log

| ID | Date | Test | Description | Severity (low/med/high) | Owner | Status (open/fixed/retested) |
|---|---|---|---|---|---|---|
| D01 | | | | | | |

## C. T11 danger-zone procedure (my module)

Zone acceptance criteria to check together with T11:

- [ ] Dashboard shows the alert within 1 s of entering a zone
- [ ] Alert clears within 2 s of exit
- [ ] ENTER and EXIT logged exactly once per crossing (no flicker)
- [ ] ESP32 applies the zone speed limit in 3 of 3 trials; double beep heard
- [ ] At least 5 zones added, edited and removed from the zone manager without restarting the server
- [ ] (Nice to have) Laptop disconnected: ESP32 still applies the limit and the alert is synced after reconnect

Run per zone (repeat for each of the 5+ zones):

1. Zone is Active in the zone manager. Note its radius and limit.
2. Cross the zone 10 times (drive in, drive out), driving below the limit.
3. Query the events table filtered by zone: expect `ZONE_ENTER` = 10 and `ZONE_EXIT` = 10.
4. Cross once above the limit: expect one `OVERSPEED_IN_ZONE` per crossing.
5. Record counts below.

| Zone | ENTER (expect 10) | EXIT (expect 10) | Overspeed event OK | Limit applied by ESP32 (3 of 3) | Result |
|---|---|---|---|---|---|
| Z01 | | | | | |
| Z02 | | | | | |
| Z03 | | | | | |
| Z04 | | | | | |
| Z05 | | | | | |
| Z06 | | | | | |

Software check for the engine logic (run from the `code` folder, no hardware needed):

```bash
python -m pytest backend/tests -q
```

The engine unit tests include a simulated T11 (10 crossings give 10 ENTER and 10 EXIT). That does not replace the physical run above.

## D. Regression checklist (run before every demo)

- [ ] `python -m pytest backend/tests -q` passes
- [ ] Zone manager page loads and shows all zones
- [ ] Add, edit, disable and delete a zone without restarting the server
- [ ] Event log filters by type and date; CSV export opens correctly
- [ ] T9 serial link stable for at least 5 minutes
- [ ] T10 alert banner under 1 s
- [ ] T11 quick check: 1 crossing gives 1 ENTER and 1 EXIT
- [ ] T12 laptop-disconnect check
- [ ] Database backup taken: `python backend/backup.py`
- [ ] Spare ESP32 and modules packed; pre-recorded demo video on the laptop

## E. Project sign-off

- [ ] All Must requirements implemented
- [ ] T1 to T13 passed and recorded
- [ ] Code merged to main with README
- [ ] Wiring diagram, pin map and this document updated to the final build
- [ ] Demo video recorded as backup

Signed off by: ______________  Date: ____________
