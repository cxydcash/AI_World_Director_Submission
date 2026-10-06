"""Discrete NPC schedules and time-of-day helpers."""

import math


SECONDS_PER_DAY = 86400.0

LOCATIONS = {
    "home": (20.0, 20.0),
    "workplace": (50.0, 50.0),
    "town": (70.0, 70.0),
}

DEFAULT_SCHEDULE = [
    {"start": 0, "end": 8 * 3600, "location": "home"},
    {"start": 8 * 3600, "end": 12 * 3600, "location": "workplace"},
    {"start": 12 * 3600, "end": 14 * 3600, "location": "town"},
    {"start": 14 * 3600, "end": 18 * 3600, "location": "workplace"},
    {"start": 18 * 3600, "end": 24 * 3600, "location": "home"},
]


def default_schedule():
    """Return a fresh copy of the default daily schedule."""
    return [dict(entry) for entry in DEFAULT_SCHEDULE]


def normalize_schedule(schedule):
    """Validate and copy a schedule into a canonical list of entries."""
    if schedule is None:
        schedule = default_schedule()
    if not isinstance(schedule, list) or not schedule:
        raise ValueError("NPC schedule must be a non-empty list.")

    entries = []
    for entry in schedule:
        if not isinstance(entry, dict):
            raise ValueError("Each schedule entry must be an object.")
        start = float(entry.get("start"))
        end = float(entry.get("end"))
        location = entry.get("location")
        if location not in LOCATIONS:
            raise ValueError(f"Unknown schedule location: {location}.")
        if not 0.0 <= start < end <= SECONDS_PER_DAY:
            raise ValueError("Schedule entries must be within one day and ordered.")
        entries.append(
            {"start": start, "end": end, "location": location}
        )

    entries.sort(key=lambda entry: entry["start"])
    for previous, current in zip(entries, entries[1:]):
        if current["start"] < previous["end"]:
            raise ValueError("Schedule entries must not overlap.")
    return entries


def time_of_day_seconds(seconds):
    """Return seconds since midnight for an absolute world time."""
    number = float(seconds)
    if not math.isfinite(number):
        raise ValueError("World time must be finite.")
    return number % SECONDS_PER_DAY


def location_at_time(schedule, seconds):
    """Return the schedule location for an absolute world time."""
    tod = time_of_day_seconds(seconds)
    for entry in schedule:
        if entry["start"] <= tod < entry["end"]:
            return entry["location"]
    return schedule[0]["location"]


def location_coordinates(location):
    """Return the fixed coordinates for a schedule location."""
    coordinates = LOCATIONS.get(location)
    if coordinates is None:
        raise ValueError(f"Unknown schedule location: {location}.")
    return (float(coordinates[0]), float(coordinates[1]))


def format_time_of_day(seconds):
    """Return HH:MM for an absolute world time."""
    tod = int(time_of_day_seconds(seconds))
    hours = tod // 3600
    minutes = (tod % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def next_schedule_change(schedule, seconds):
    """Return (absolute_seconds, next_location) of the next location change."""
    current_location = location_at_time(schedule, seconds)
    tod = time_of_day_seconds(seconds)
    day = math.floor(float(seconds) / SECONDS_PER_DAY)

    same_day = [
        entry for entry in schedule
        if float(entry["start"]) > tod and entry["location"] != current_location
    ]
    if same_day:
        next_entry = min(same_day, key=lambda entry: float(entry["start"]))
        return day * SECONDS_PER_DAY + float(next_entry["start"]), next_entry["location"]

    next_day = [
        entry for entry in schedule
        if entry["location"] != current_location
    ]
    if not next_day:
        return None
    next_entry = min(next_day, key=lambda entry: float(entry["start"]))
    return (day + 1) * SECONDS_PER_DAY + float(next_entry["start"]), next_entry["location"]