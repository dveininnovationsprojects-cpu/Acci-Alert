"""
AcciAlert - Geographic & Spatial Utilities
Haversine calculations, geofence checks, and coordinate projections.
Author: Muniyappan
"""

import math

EARTH_RADIUS_METERS = 6371000.0  # Earth's radius in meters


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points on the Earth's surface
    using the Haversine formula. Returns distance in meters.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return EARTH_RADIUS_METERS * c


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the forward azimuth (initial bearing in degrees from 0-360)
    from point 1 to point 2.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2) -
         math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda))

    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def move_coordinate(lat: float, lon: float, bearing_deg: float, distance_meters: float) -> tuple:
    """
    Given a starting point, bearing in degrees, and distance in meters,
    compute the new destination latitude and longitude.
    """
    angular_distance = distance_meters / EARTH_RADIUS_METERS
    bearing_rad = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)

    phi2 = math.asin(
        math.sin(phi1) * math.cos(angular_distance) +
        math.cos(phi1) * math.sin(angular_distance) * math.cos(bearing_rad)
    )

    lambda2 = lambda1 + math.atan2(
        math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(phi1),
        math.cos(angular_distance) - math.sin(phi1) * math.sin(phi2)
    )

    return (math.degrees(phi2), (math.degrees(lambda2) + 540.0) % 360.0 - 180.0)


def find_active_danger_zone(lat: float, lon: float, zones: list) -> tuple:
    """
    Check if a given (lat, lon) is inside any danger zone.
    Returns (zone_dict_or_None, distance_meters)
    If inside multiple zones, returns the one with the smallest distance to center.
    """
    active_zone = None
    min_dist = float("inf")

    for zone in zones:
        try:
            z_lat = float(zone.get("latitude", 0))
            z_lon = float(zone.get("longitude", 0))
            radius = float(zone.get("radius_meters", 100))

            dist = haversine_distance(lat, lon, z_lat, z_lon)
            if dist <= radius:
                if dist < min_dist:
                    min_dist = dist
                    active_zone = zone
        except (ValueError, TypeError):
            continue

    return active_zone, (min_dist if active_zone else None)

