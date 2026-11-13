"""
AcciAlert - Automated Test Suite
Verifies all REST endpoints, database queries, simulation physics,
geofencing, and serialization.
Author: Muniyappan
"""

import sys
import os
from pathlib import Path
import json
import unittest
import time

# Ensure backend directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import app, simulator, state, send_unified_command
import database
import geo_utils


class TestAcciAlertSystem(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        database.init_db()

    def test_01_index_html(self):
        """Verify homepage loads with HTTP 200 and required elements."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8")
        self.assertIn("AcciAlert", html)
        self.assertIn("Muniyappan", html)
        self.assertIn("val-speed", html)
        self.assertIn("val-alcohol", html)
        self.assertIn("val-obstacle", html)
        self.assertIn("val-tilt", html)
        self.assertIn("leaflet-map", html)
        self.assertIn("speedChartCanvas", html)
        self.assertIn("alcoholChartCanvas", html)

    def test_02_status_api(self):
        """Verify /api/status returns operational status."""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["status"], "success")
        self.assertIn("mode", data)
        self.assertIn("active_driver", data)
        self.assertIn(data["active_driver"], ["Unknown", "Muniyappan"])

    def test_03_zones_api_crud(self):
        """Verify danger zone creation, listing, and deletion."""
        # GET zones
        res = self.client.get("/api/zones")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(len(data["zones"]) >= 4)

        # POST new zone
        new_zone = {
            "code": "Z99",
            "name": "Test Test Curve",
            "latitude": 13.055,
            "longitude": 80.255,
            "radius_meters": 120,
            "speed_limit": 25,
            "severity": "CRITICAL",
            "details": "Automated test zone"
        }
        res_post = self.client.post("/api/zones", json=new_zone)
        self.assertEqual(res_post.status_code, 200)
        post_data = json.loads(res_post.data)
        self.assertTrue(post_data["status"] == "success")
        zone_id = post_data["id"]

        # DELETE created zone
        res_del = self.client.delete(f"/api/zones/{zone_id}")
        self.assertEqual(res_del.status_code, 200)
        del_data = json.loads(res_del.data)
        self.assertEqual(del_data["status"], "success")

    def test_04_drivers_api(self):
        """Verify /api/drivers listing and addition."""
        res = self.client.get("/api/drivers")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(len(data["drivers"]) >= 3)

        # Add / update driver
        res_post = self.client.post("/api/drivers", json={
            "name": "Muniyappan",
            "license_number": "TN-02-2023-009871",
            "authorized": 1,
            "notes": "Verified primary pilot",
            "make_active": True
        })
        self.assertEqual(res_post.status_code, 200)

    def test_05_command_api(self):
        """Verify /api/command endpoint for vehicle controls."""
        # STOP
        res = self.client.post("/api/command", json={"command": "STOP"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(simulator.state, "EMERGENCY")
        self.assertEqual(simulator.speed, 0.0)

        # RESET
        res = self.client.post("/api/command", json={"command": "RESET"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(simulator.state, "RUNNING")

        # LIMIT
        res = self.client.post("/api/command", json={"command": "LIMIT", "value": 35})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(simulator.speed_limit, 35.0)

        # SIGNAL
        res = self.client.post("/api/command", json={"command": "SIGNAL", "value": "RED"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(simulator.signal, "RED")

        # AUTH
        res = self.client.post("/api/command", json={"command": "AUTH", "value": 0})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(simulator.auth, 0)

    def test_06_simulation_scenarios(self):
        """Verify simulator scenarios trigger expected sensor states."""
        # Accident
        res = self.client.post("/api/simulation/scenario", json={"scenario": "accident"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(simulator.tilt >= 45.0)
        self.assertEqual(simulator.state, "EMERGENCY")

        # Alcohol
        res = self.client.post("/api/simulation/scenario", json={"scenario": "alcohol"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(simulator.alcohol_adc >= 600)

        # Obstacle
        res = self.client.post("/api/simulation/scenario", json={"scenario": "obstacle"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(simulator.obstacle_dist <= 40)

        # Reset to normal
        res = self.client.post("/api/simulation/scenario", json={"scenario": "normal"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(simulator.state, "RUNNING")

    def test_07_events_api_and_csv_export(self):
        """Verify events logging, retrieval, and CSV download."""
        # Add test event directly
        event_id = database.add_event(
            event_type="ACCIDENT",
            driver_name="Muniyappan",
            vehicle_speed=45.2,
            latitude=13.0827,
            longitude=80.2707,
            danger_zone_name="Anna Salai School Zone",
            details="Test impact event"
        )
        self.assertTrue(event_id > 0)

        # Query events
        res = self.client.get("/api/events?type=ACCIDENT")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data["data"]["total"] >= 1)

        # Export CSV
        res_csv = self.client.get("/api/events/export?type=ACCIDENT")
        self.assertEqual(res_csv.status_code, 200)
        self.assertEqual(res_csv.content_type, "text/csv; charset=utf-8")
        csv_text = res_csv.data.decode("utf-8")
        self.assertIn("Event ID", csv_text)
        self.assertIn("ACCIDENT", csv_text)
        self.assertIn("Muniyappan", csv_text)

    def test_08_geo_calculations(self):
        """Verify Haversine distance and geofence detection."""
        # Distance between two known points in Chennai
        dist = geo_utils.haversine_distance(13.0827, 80.2707, 13.0827, 80.2717)
        self.assertTrue(100 < dist < 120)

        # Geofence detection
        zones = database.get_all_zones()
        zone, dist = geo_utils.find_active_danger_zone(13.0827, 80.2707, zones)
        self.assertIsNotNone(zone)
        self.assertEqual(zone["code"], "Z01")

    def test_09_serial_ports_api(self):
        """Verify /api/serial/ports enumeration."""
        res = self.client.get("/api/serial/ports")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "success")
        self.assertIsInstance(data["ports"], list)


if __name__ == "__main__":
    unittest.main()

