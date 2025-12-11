import os
import json
import requests
import re
from datetime import datetime, timedelta
from dateutil import parser as dtparse
from dateutil import tz
import html

def fetch_m3u(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text

# Parse an extinf line into object
def parse_extinf(line):
    m = re.match(r'#EXTINF:-1\s*(?P<attrs>[^,]*),(?P<label>.*)', line)
    if not m:
        return None
    attrs_str = m.group('attrs')
    label = m.group('label').strip()
    pairs = re.findall(r'([\w-]+)="([^"]*)"', attrs_str)  # capture keys like tvg-chno
    attrs = {k: v for k, v in pairs}
    return {
        "tvg-chno": attrs.get("tvg-chno"),
        "tvg-id": attrs.get("tvg-id"),
        "tvg-name": attrs.get("tvg-name") or label
    }

def parse_timezone(timezone_str):

    if not timezone_str:
        return tz.UTC
    
    # Handle UTC offset formats like "UTC+1" or "UTC-5"
    utc_offset_match = re.match(r'^UTC([+-]\d+)$', timezone_str, re.IGNORECASE)
    if utc_offset_match:
        offset_hours = int(utc_offset_match.group(1))
        return tz.tzoffset(None, offset_hours * 3600)
    
    # Try parsing as named timezone
    try:
        parsed_tz = tz.gettz(timezone_str)
        if parsed_tz:
            return parsed_tz
    except:
        pass
    
    # Default to UTC if unable to parse
    return tz.UTC

def format_xmltv_timestamp(dt):
    return dt.strftime("%Y%m%d%H%M%S %z")

def parse_event_from_name(name):

    with open("/config/patterns.json", "r") as f:
        PATTERNS = json.load(f)

    for pattern_cfg in PATTERNS:
        # Check no_event_pattern first
        no_event_pattern = pattern_cfg.get("no_event_pattern")
        no_match = re.search(no_event_pattern, name, re.IGNORECASE)
        if no_event_pattern and no_match:
            channel_number = no_match.group("channel_number").strip()
            return {
                "event_name": None,
                "event_dt_utc": None,
                "xmltv_start": None,
                "matched_pattern": pattern_cfg["name"],
                "channel_name": pattern_cfg.get("channel_name") + f" {channel_number}",
                "icon_url": pattern_cfg.get("icon_url"),
                "category": pattern_cfg.get("category")
            }

        # Try main pattern
        pattern = pattern_cfg.get("pattern")
        if not pattern:
            continue

        match = re.search(pattern, name, re.IGNORECASE)
        if not match:
            continue

        event_name = match.group("event_name").strip()
        datetime_str = match.group("datetime").strip()
        channel_number = match.group("channel_number").strip() 
        datetime_format = pattern_cfg.get("datetime_format", "%Y-%m-%d %H:%M")
        timezone_str = pattern_cfg.get("timezone", "UTC")

        # Parse timezone
        local_tz = parse_timezone(timezone_str)

        # Parse datetime
        try:
            # Try parsing with the specified format first
            try:
                event_dt = datetime.strptime(datetime_str, datetime_format)
            except ValueError:
                # If it fails and format includes minutes, try without minutes (for times like "7pm")
                if ":%M" in datetime_format:
                    fallback_format = datetime_format.replace(":%M", "")
                    event_dt = datetime.strptime(datetime_str, fallback_format)
                else:
                    raise
            
            # If no year in format, set to current year
            if "%Y" not in datetime_format:
                event_dt = event_dt.replace(year=datetime.now().year)
            
            # If no date provided, use today's date
            if not pattern_cfg.get("date_provided", True):
                today = datetime.now()
                if os.getenv("TZ"):
                    today = today.astimezone(parse_timezone(os.getenv("TZ")))
                event_dt = event_dt.replace(year=today.year, month=today.month, day=today.day)
            
            event_dt_local = event_dt.replace(tzinfo=local_tz)
            event_dt_utc = event_dt_local.astimezone(tz.UTC)
            
            xmltv_start = format_xmltv_timestamp(event_dt_utc)
            
        except ValueError:
            continue  # unable to parse, try next pattern

        return {
            "event_name": event_name,
            "event_dt_utc": event_dt_utc,
            "xmltv_start": xmltv_start,
            "matched_pattern": pattern_cfg["name"],
            "channel_name": pattern_cfg.get("channel_name") + f" {channel_number}",
            "icon_url": pattern_cfg.get("icon_url"),
            "category": pattern_cfg.get("category")
        }
    
    print(f"No pattern matched for channel name: {name}")
    return None  # no pattern matched

def parse_channels(m3u_text):

    channels = []
    lines = m3u_text.splitlines()
    
    current_extinf = None

    for line in lines:
        line = line.strip()
        
        # Check if this is an EXTINF line
        if line.startswith("#EXTINF"):
            current_extinf = parse_extinf(line)
            continue
        
        # Check if this is a stream URL (non-comment, non-empty line following EXTINF)
        if current_extinf and line and not line.startswith("#"):
            name = current_extinf["tvg-name"]
            channel_id = current_extinf["tvg-id"]
            channel_number = current_extinf["tvg-chno"]
            stream_url = line

            event_info = parse_event_from_name(name)
            if not event_info or not event_info["event_name"]:
                channels.append({
                    "id": channel_number,
                    "channel_name": event_info["channel_name"] if event_info else name,
                    "icon_url": event_info["icon_url"] if event_info else "",
                    "category": event_info["category"] if event_info else None,
                    "program_start": None,
                    "program_stop": None,
                    "program_name": None,
                    "description": None,
                    "stream_url": stream_url
                })
                current_extinf = None
                continue

            channels.append({
                "id": channel_number,
                "channel_name": event_info["channel_name"],
                "program_name": event_info["event_name"],
                "icon_url": event_info["icon_url"],
                "category": event_info["category"],
                "program_start": event_info["xmltv_start"],
                "program_stop": event_info.get("xmltv_stop"),
                "description": name,
                "stream_url": stream_url
            })
            
            current_extinf = None

    return channels

def has_open_slot(programs, start_dt, stop_dt):
    """
    Check if a time slot is available (no overlapping events)
    Returns True if the slot is open, False if there's a conflict
    """
    for program in programs:
        # Check for overlap: new event starts before existing ends AND new event ends after existing starts
        if start_dt < program["stop_dt"] and stop_dt > program["start_dt"]:
            return False  # Conflict found
    return True  # No conflicts

def create_combined_channels(channels):
    with open("/config/patterns.json", "r") as f:
        PATTERNS = json.load(f)

    combined_channels = {}
    for pattern_cfg in PATTERNS:
        if not combined_channels.get(pattern_cfg["name"]):
            combined_channels[pattern_cfg["name"]] = {
                "channel_name": pattern_cfg.get("channel_name"),
                "icon_url": pattern_cfg.get("icon_url"),
                "category": pattern_cfg.get("category"),
                "num_combined_channels": pattern_cfg.get("num_combined_channels", 10),
                "service_channels": []
            }
            for i in range(0, pattern_cfg.get("num_combined_channels", 10)):
                combined_channels[pattern_cfg["name"]]["service_channels"].append({
                    "channel_number": i + 1,
                    "id": f"{pattern_cfg.get('channel_name').lower().replace(' ', '_').replace('+', '_plus')}_{i+1}",
                    "channel_name": f"{pattern_cfg.get('channel_name')} {i+1}",
                    "icon_url": pattern_cfg.get("icon_url"),
                    "category": pattern_cfg.get("category"),
                    "programs": []
                })

    # Separate channels with events from those without
    channels_with_events = [ch for ch in channels if ch["program_start"]]
    
    # Sort by start time
    channels_with_events.sort(key=lambda x: x["program_start"])
    
    # Distribute events across combined channels
    for channel in channels_with_events:
        matched_pattern = None
        for pattern_cfg in PATTERNS:
            if channel["channel_name"].startswith(pattern_cfg.get("channel_name", "")):
                matched_pattern = pattern_cfg["name"]
                break
        
        if not matched_pattern:
            print(f"No matched channel pattern for channel: {channel['channel_name']}")
            continue
        
        # Parse event times
        start_dt = datetime.strptime(channel["program_start"], "%Y%m%d%H%M%S %z")
        
        if channel.get("program_stop"):
            stop_dt = datetime.strptime(channel["program_stop"], "%Y%m%d%H%M%S %z")
        else:
            stop_dt = start_dt + timedelta(hours=3)
        
        # Find first available combined channel (no time conflict)
        assigned = False
        for service_channel in combined_channels[matched_pattern]["service_channels"]:
            if has_open_slot(service_channel["programs"], start_dt, stop_dt):
                # Add event to this channel
                service_channel["programs"].append({
                    "start_dt": start_dt,
                    "stop_dt": stop_dt,
                    "start_str": channel["program_start"],
                    "stop_str": format_xmltv_timestamp(stop_dt),
                    "original_channel_id": channel["id"],
                    "stream_url": channel["stream_url"],
                    "program_name": channel["program_name"],
                    "description": channel["description"],
                    "icon_url": channel["icon_url"]
                })
                assigned = True
                break
        
        if not assigned:
            print(f"Warning: Could not assign event '{channel['program_name']}' - all combined channels full")
    
    # Sort programs in each channel by start time
    for pattern_name in combined_channels:
        for service_channel in combined_channels[pattern_name]["service_channels"]:
            service_channel["programs"].sort(key=lambda x: x["start_dt"])
    
    return combined_channels

def save_schedule(combined_channels):
    """Save the schedule to a JSON file for the web server to read"""
    # Convert datetime objects to strings for JSON serialization
    serializable_schedule = {}
    
    for pattern_name, pattern_data in combined_channels.items():
        serializable_schedule[pattern_name] = {
            "channel_name": pattern_data["channel_name"],
            "icon_url": pattern_data["icon_url"],
            "category": pattern_data["category"],
            "num_combined_channels": pattern_data["num_combined_channels"],
            "service_channels": []
        }
        
        for service_channel in pattern_data["service_channels"]:
            serializable_channel = {
                "channel_number": service_channel["channel_number"],
                "id": service_channel["id"],
                "channel_name": service_channel["channel_name"],
                "icon_url": service_channel["icon_url"],
                "category": service_channel["category"],
                "programs": []
            }
            
            for program in service_channel["programs"]:
                serializable_program = {
                    "start_dt": program["start_dt"].isoformat(),
                    "stop_dt": program["stop_dt"].isoformat(),
                    "start_str": program["start_str"],
                    "stop_str": program["stop_str"],
                    "original_channel_id": program["original_channel_id"],
                    "stream_url": program["stream_url"],
                    "program_name": program["program_name"],
                    "description": program["description"],
                    "icon_url": program["icon_url"]
                }
                serializable_channel["programs"].append(serializable_program)
            
            serializable_schedule[pattern_name]["service_channels"].append(serializable_channel)
    
    with open("/output/schedule.json", "w", encoding="utf-8") as f:
        json.dump(serializable_schedule, f, indent=2)
    
    print(f"Schedule saved with {sum(len(p['service_channels']) for p in serializable_schedule.values())} combined channels")

def generate_xmltv(channels, output_file):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<tv generator-info-name="epgBuilder">\n')

        for channel in channels:
            channel_name_escaped = html.escape(channel["channel_name"])
            description_escaped = html.escape(channel["description"] or "")
            f.write(f'  <channel id="{channel["id"]}">\n')
            f.write(f'    <display-name>{channel_name_escaped}</display-name>\n')
            f.write(f'    <display-name>{description_escaped}</display-name>\n')
            if channel["icon_url"]:
                f.write(f'    <icon src="{channel["icon_url"]}"/>\n')
            f.write('  </channel>\n')

        for channel in channels:
            if not channel["program_start"]:
                # Create a dummy event
                start_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz.UTC)
                stop_time = start_time + timedelta(minutes=1)
                start_time_str = start_time.strftime("%Y%m%d%H%M%S %z")
                stop_time_str = stop_time.strftime("%Y%m%d%H%M%S %z")
                
                f.write(f'  <programme channel="{channel["id"]}" start="{start_time_str}" stop="{stop_time_str}">\n')
                f.write(f'    <title>No Events</title>\n')
                f.write(f'    <desc>No program information available</desc>\n')
                f.write('  </programme>\n')
                continue

            # Actual event
            start_time = datetime.strptime(channel["program_start"], "%Y%m%d%H%M%S %z")
            stop_time = start_time + timedelta(hours=3)
            stop_time_str = stop_time.strftime("%Y%m%d%H%M%S %z")
            
            # Escape special XML characters
            program_name_escaped = html.escape(channel["program_name"])
            description_escaped = html.escape(channel["description"])
            
            f.write(f'  <programme channel="{channel["id"]}" start="{channel["program_start"]}" stop="{stop_time_str}">\n')
            f.write(f'    <title>{program_name_escaped}</title>\n')
            f.write(f'    <desc>{description_escaped}</desc>\n')
            
            # Add category (e.g., Sports, Movies, etc.)
            if channel.get("category"):
                category_escaped = html.escape(channel["category"])
                f.write(f'    <category lang="en">{category_escaped}</category>\n')
            
            # Add poster/icon for the program
            if channel.get("icon_url"):
                f.write(f'    <icon src="{channel["icon_url"]}"/>\n')
            f.write('  </programme>\n')

        f.write('</tv>\n')


def generate_m3u(channels, output_file):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write('#EXTM3U\n')
        for channel in channels:
            extinf_line = f'#EXTINF:-1 tvg-chno="{channel["id"]}" tvg-id="{channel["id"]}" tvg-name="{channel["channel_name"]}" tvg-icon="{channel["icon_url"]}",{channel["channel_name"]}\n'
            f.write(extinf_line)
            stream_url = f'{channel["stream_url"]}\n'
            f.write(stream_url)

def generate_combined_xmltv(combined_channels, output_file):
    """Generate XMLTV file for combined channels with full program schedules"""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<tv generator-info-name="epgBuilder">\n')

        # Write channel definitions
        for pattern_name, pattern_data in combined_channels.items():
            for service_channel in pattern_data["service_channels"]:
                channel_name_escaped = html.escape(str(service_channel["channel_name"]), quote=False)
                
                f.write(f'  <channel id="{html.escape(str(service_channel["id"]), quote=True)}">\n')
                f.write(f'    <display-name>{channel_name_escaped}</display-name>\n')
                if service_channel.get("icon_url"):
                    icon_url_escaped = html.escape(str(service_channel["icon_url"]), quote=True)
                    f.write(f'    <icon src="{icon_url_escaped}"/>\n')
                f.write('  </channel>\n')

        # Write programme listings
        now_utc = datetime.now(tz.UTC)
        period_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=6)
        
        for pattern_name, pattern_data in combined_channels.items():
            for service_channel in pattern_data["service_channels"]:
                programs = service_channel["programs"]
                channel_id_escaped = html.escape(str(service_channel["id"]), quote=True)
                
                # Skip channels with no programs
                if not programs:
                    continue
                
                # Add hidden event for IPTV player detection (00:00 UTC +6 days, 30 min)
                hidden_start = period_start.replace(hour=0, minute=0, second=0, microsecond=0)
                hidden_stop = hidden_start + timedelta(minutes=30)
                hidden_start_str = format_xmltv_timestamp(hidden_start)
                hidden_stop_str = format_xmltv_timestamp(hidden_stop)
                
                f.write(f'  <programme channel="{channel_id_escaped}" start="{hidden_start_str}" stop="{hidden_stop_str}">\n')
                f.write(f'    <title>{html.escape(str(service_channel["channel_name"]), quote=False)} - Hidden Event</title>\n')
                f.write('    <desc>Hidden event for IPTV player channel detection (future placeholder)</desc>\n')
                if service_channel.get("category"):
                    category_escaped = html.escape(str(service_channel["category"]), quote=False)
                    f.write(f'    <category lang="en">{category_escaped}</category>\n')
                if service_channel.get("icon_url"):
                    icon_url_escaped = html.escape(str(service_channel["icon_url"]), quote=True)
                    f.write(f'    <icon src="{icon_url_escaped}"/>\n')
                f.write('  </programme>\n')
                
                # Write only real events (no gap filling)
                for program in programs:
                    # Write the actual event - FORCE ESCAPE ALL TEXT CONTENT
                    # Make absolutely sure we're escaping by converting to string first
                    raw_program_name = str(program.get("program_name", ""))
                    raw_description = str(program.get("description", ""))
                    
                    # Force escape - this should convert & to &amp;
                    program_name_escaped = html.escape(raw_program_name, quote=False)
                    description_escaped = html.escape(raw_description, quote=False)
                    
                    f.write(f'  <programme channel="{channel_id_escaped}" start="{program["start_str"]}" stop="{program["stop_str"]}">\n')
                    f.write(f'    <title>{program_name_escaped}</title>\n')
                    f.write(f'    <desc>{description_escaped}</desc>\n')
                    
                    if service_channel.get("category"):
                        category_escaped = html.escape(str(service_channel["category"]), quote=False)
                        f.write(f'    <category lang="en">{category_escaped}</category>\n')
                    
                    if program.get("icon_url"):
                        icon_url_escaped = html.escape(str(program["icon_url"]), quote=True)
                        f.write(f'    <icon src="{icon_url_escaped}"/>\n')
                    
                    f.write('  </programme>\n')

        f.write('</tv>\n')
    
    print(f"Combined XMLTV generated: {output_file}")

