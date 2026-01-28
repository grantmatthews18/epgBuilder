import unicodedata
import re
from datetime import datetime
import dateparser
from difflib import SequenceMatcher
from datetime import timedelta
import uuid
from dateutil import tz as dateutil_tz

import app.globals as globals


# Channel to event processing functions
# Apply manual rules to the input string
def process_manual_rules(input_string, rules):
    for pattern, replacement in rules:
        input_string = re.sub(pattern, replacement, input_string)
    return input_string

# Normalize the input string (lowercase, remove special characters, standardize date/time)
def normalize_input(input_string):

    # Convert to lowercase
    input_string = input_string.lower()

    # Normalize unicode characters
    input_string = unicodedata.normalize("NFKD", input_string)

    # Replace specific characters with spaces or remove them
    replacements = {
        "|": " ",
        "@": " ",
        "—": " ",
        "–": " ",
        "/": " / ",
        ",": " ",
        ".": " ",
        "(": " ",
        ")": " ",
    }

    for k, v in replacements.items():
        input_string = input_string.replace(k, v)
    
    # Replace hyphens with spaces, but preserve hyphens in dates (YYYY-MM-DD format)
    # This regex replaces hyphens that are NOT between digits
    input_string = re.sub(r'(?<!\d)-|-(?!\d)', ' ', input_string)

    # Remove any remaining non-alphanumeric characters except spaces
    input_string = re.sub(r"\s+", " ", input_string).strip()

    # Normalize time formats (e.g., convert "3 pm" to "15:00")
    TIME_REGEX = re.compile(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    )

    def rep(match):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)

        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

        return f"{hour:02d}:{minute:02d}"

    input_string = TIME_REGEX.sub(rep, input_string)

    # Normalize date formats (e.g., convert "Jan 5 2023" to "2023-01-05")
    tokens = input_string.split()
    out = []

    CURRENT_YEAR = datetime.now().year

    i = 0
    while i < len(tokens):
        chunk = " ".join(tokens[i:i+4])
        try:
            dt = dateparser.parse(
                chunk,
                default=datetime(CURRENT_YEAR, 1, 1),
                fuzzy=False
            )
            if dt:
                out.append(dt.strftime("%Y-%m-%d"))
                i += len(chunk.split())
                continue
        except Exception:
            pass

        out.append(tokens[i])
        i += 1
    
    input_string = " ".join(out)

    # Final Cleanup
    input_string = re.sub(r"[^\w:\-/ ]", "", input_string)
    input_string = re.sub(r"\s+", " ", input_string).strip()

    return input_string

# Extract date from the input string, remove it, and return both date and modified string
def extract_date(input_string):

    date_pattern = r'\b(\d{4})-(\d{2})-(\d{2})\b'
    matches = re.findall(date_pattern, input_string)
    if matches:
        # Parse all dates and find the earliest one
        dates = [datetime.strptime(f"{y}-{m}-{d}", "%Y-%m-%d") for y, m, d in matches]
        earliest_date = min(dates)
        date_str = earliest_date.strftime("%Y-%m-%d")
        # Remove all date instances from the string
        input_string = re.sub(date_pattern, '', input_string).strip()
        # Clean up multiple spaces
        input_string = re.sub(r'\s+', ' ', input_string)
    else:
        date_str = None
    
    return (date_str, input_string)

# Extract time from the input string, remove it, and return both time and modified string
def extract_time(input_string):
    # Use non-capturing groups to get full time strings instead of tuples
    time_pattern = r'\b(?:[0-1][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?\b'
    matches = re.findall(time_pattern, input_string)
    
    if matches:
        # Parse all times and convert to datetime for comparison
        times = []
        for time_str in matches:
            # Parse time (handle both HH:MM and HH:MM:SS formats)
            if ':' in time_str:
                parts = time_str.split(':')
                if len(parts) == 2:
                    times.append(datetime.strptime(time_str, "%H:%M"))
                elif len(parts) == 3:
                    times.append(datetime.strptime(time_str, "%H:%M:%S"))
        
        # Find earliest and latest times
        earliest_time = min(times)
        latest_time = max(times)
        
        # Check if times differ by more than 1 minute
        time_diff = (latest_time - earliest_time).total_seconds() / 60
        
        if time_diff > 1:
            start_time = earliest_time.strftime("%H:%M")
            end_time = latest_time.strftime("%H:%M")
        else:
            start_time = earliest_time.strftime("%H:%M")
            end_time = None
        
        # Remove all time instances from the string
        input_string = re.sub(time_pattern, '', input_string).strip()
        # Clean up multiple spaces
        input_string = re.sub(r'\s+', ' ', input_string)
    else:
        start_time = None
        end_time = None

    return (start_time, end_time, input_string)

# Convert start_time and end_time from tz to UTC
def convert_to_utc(date_str, start_time, end_time=None, tz_name=None):
    """Convert start_time (and optional end_time) on date_str from tz_name to UTC.

    Returns a tuple: (date_str_utc, start_time_utc, end_time_utc_or_None).

    Behavior:
    - date_str and start_time are required.
    - If end_time is provided and is earlier than start_time, it is assumed the event crosses midnight
      and the end date is date_str + 1 before conversion.
    - If tz_name is falsy or cannot be resolved, the function will return the input values formatted
      (normalized) without applying any timezone conversion.
    """
    if not date_str or not start_time:
        raise ValueError("date_str and start_time are required")

    # Helper to parse combined date+time with optional seconds
    def _parse_datetime(d_str, t_str):
        fmt = "%Y-%m-%d %H:%M:%S" if t_str.count(":") == 2 else "%Y-%m-%d %H:%M"
        return datetime.strptime(f"{d_str} {t_str}", fmt)

    # If no timezone provided, return normalized/validated values (no conversion)
    if not tz_name:
        start_dt = _parse_datetime(date_str, start_time)
        date_out = start_dt.strftime("%Y-%m-%d")
        start_out = start_dt.strftime("%H:%M")
        end_out = None
        if end_time:
            end_dt = _parse_datetime(date_str, end_time)
            # If end time is earlier than start time, move end to next day
            if end_dt < start_dt:
                end_dt += timedelta(days=1)
            end_out = end_dt.strftime("%H:%M")
        return date_out, start_out, end_out

    local_zone = dateutil_tz.gettz(tz_name) or dateutil_tz.UTC
    utc_zone = dateutil_tz.UTC

    # Parse and attach local tz
    start_naive = _parse_datetime(date_str, start_time)
    start_local = start_naive.replace(tzinfo=local_zone)
    start_utc = start_local.astimezone(utc_zone)

    date_utc = start_utc.strftime("%Y-%m-%d")
    start_time_utc = start_utc.strftime("%H:%M")

    end_time_utc = None
    if end_time:
        end_naive = _parse_datetime(date_str, end_time)
        # If end time is earlier than the start time, assume it crosses midnight
        if end_naive < start_naive:
            end_naive = end_naive + timedelta(days=1)
        end_local = end_naive.replace(tzinfo=local_zone)
        end_utc = end_local.astimezone(utc_zone)
        end_time_utc = end_utc.strftime("%H:%M")

    return date_utc, start_time_utc, end_time_utc

# Find the best matching event from the database
def find_best_match(normalized_string, events):

    if not events:
        return None, 0
    
    best_match = None
    best_ratio = 0
    
    # Normalize the input string for comparison
    normalized_input = normalized_string.lower().strip()
    
    for event in events:
        event_name_db = event.get('event_name', '')
        event_name_db = (event_name_db or '').lower().strip()

        # Calculate similarity ratio using SequenceMatcher
        ratio = SequenceMatcher(None, normalized_input, event_name_db).ratio()
        match_percentage = ratio * 100

        # Update best match if this is better
        if match_percentage > best_ratio:
            best_ratio = match_percentage
            best_match = event
    
    return (best_match, best_ratio)

# Makes string safe as a filename
def make_safe_filename(input_string):
    # Replace spaces with underscores and remove invalid characters
    safe_string = input_string.replace(" ", "_")
    safe_string = re.sub(r'[^\w\-_\.]', '', safe_string)
    return safe_string

# Capitalize significant words in event names
def format_event_name(input_string):
    # Split into words and capitalize first letter of each word
    words = input_string.split()
    formatted_words = [word.upper() if len(word) > 2 else word for word in words]
    return ' '.join(formatted_words)


def auto_extract(event_string):
    # Normalize the input event string
    normalized_string = normalize_input(event_string)

    # Extract date information
    (date_str, input_string) = extract_date(normalized_string)

    if not date_str:
        print(f"Failed to extract date from {event_string}")
        return None

    # Extract time information
    (start_time, end_time, input_string) = extract_time(input_string)

    return (input_string, date_str, start_time, end_time)

# Get event data from the input string
def get_event_data(event_string, pattern = None, tz = None):

    pattern_matched = False
    input_string = None
    date_str = None
    start_time = None
    end_time = None

    # Apply manual rules if any
    if pattern:
        re_pattern = re.compile(pattern)
        match = re_pattern.search(event_string)

        if (not match) or (not match.group("event_name") or not match.group("date_str") or not match.group("start_time")):
            (input_string, date_str, start_time, end_time) = auto_extract(event_string)
        else:
            input_string = match.group("event_name")
            date_str = match.group("date_str")
            start_time = match.group("start_time")
            end_time = match.group("end_time") if "end_time" in match.groupdict() else None
            pattern_matched = True
    # If no pattern, fallback to automatic extraction
    else:
       (input_string, date_str, start_time, end_time) = auto_extract(event_string)

    # Convert date_str, start_time and end_time to UTC if TZ specified
    if tz and start_time:
        date_str_utc, start_time_utc, end_time_utc = convert_to_utc(date_str, start_time, end_time, tz)

        date_str = date_str_utc
        start_time = start_time_utc
        end_time = end_time_utc

    # Get all events for specific date from database
    events_on_date = globals.ESPN_DB_MANAGER.fetch_events_by_date(date_str)

    # Match event string with database entries to find the best match
    (matched_event, match_percentage) = find_best_match(input_string, events_on_date)

    if match_percentage >= globals.CONFIG.get("min_match_percentage", 50) or (globals.CONFIG.get("always_accept_pattern_matched_events", False) and pattern_matched):
        if(match_percentage >= globals.CONFIG.get("min_match_percentage", 50)):
            print(f"Event {event_string} Auto-Matched to Event: {matched_event.get('event_name', 'undefined')} with Match Percentage: {match_percentage}")
            return matched_event
        else:
            print(f"Event {event_string} not Auto-Matched. Manually extracted fields: {input_string}, {date_str}, {start_time}, {end_time}")
            return {
                "event_id": make_safe_filename(input_string),
                "league": None,
                "event_name": input_string,
                "date": date_str,
                "start_time": start_time,
                "end_time": end_time,
                "home_team": None,
                "away_team": None,
                "home_icon": None,
                "away_icon": None
            }
