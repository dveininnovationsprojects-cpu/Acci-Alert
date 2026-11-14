# AcciAlert Database Directory

This directory contains the central SQLite database and schema specifications for the **AcciAlert Smart Driver Safety & Biometric System**.

## Database Files
- **`accialert.db`**: Primary unified SQLite database containing registered drivers, safety events, danger zones, commands log, and telemetry records.
- **`schema.sql`**: Formal SQL DDL schema definitions for all system tables.

## Tables Overview

### 1. `drivers`
Stores biometrically enrolled driver records. When a driver is enrolled via the Web Cockpit Wizard or CLI enrolment tool, their details are automatically stored here:
* `id` (INTEGER, PK): Unique auto-incrementing driver ID.
* `name` (TEXT): Driver's full name (e.g. `Arsal`, `Muniyappan`, `Arun`).
* `license_number` (TEXT, UNIQUE): State driver license or generated badge ID.
* `authorized` (INTEGER): `1` for authorized vehicle operators, `0` for unauthorized/blocked.
* `notes` (TEXT): Number of captured face descriptors or shift notes.
* `created_at` (TEXT): ISO-8601 enrolment timestamp.

### 2. `events`
Security and Ignition Interlock audit trail:
* `id`, `event_type`, `timestamp`, `driver_name`, `vehicle_speed`, `latitude`, `longitude`, `danger_zone_name`, `details`.
* Includes biometric verification scores (`Distance: X.XX, Confidence: XX.X%`).

### 3. `danger_zones`
Geofencing safety zones for dynamic speed governor interlock.

### 4. `commands_log`
Actuation commands sent to ESP32 serial interface (`AUTH`, `LIMIT`).

