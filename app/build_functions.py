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
        if no_event_pattern and re.search(no_event_pattern, name, re.IGNORECASE):
            return {
                "event_name": None,
                "event_dt_utc": None,
                "xmltv_start": None,
                "matched_pattern": pattern_cfg["name"],
                "channel_name": pattern_cfg.get("channel_name"),
                "icon_url": pattern_cfg.get("icon_url")
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
            event_dt = datetime.strptime(datetime_str, datetime_format)
            
            # If no year in format, set to current year
            if "%Y" not in datetime_format:
                event_dt = event_dt.replace(year=datetime.now().year)
            
            # If no date provided, use today's date
            if not pattern_cfg.get("date_provided", True):
                today = datetime.now()
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
            "icon_url": pattern_cfg.get("icon_url")
        }
    
    return None  # no pattern matched

def parse_channels(m3u_text):

    channels = []
    lines = m3u_text.splitlines()

    for line in lines:
        # Confirm line is an EXTINF line
        match = re.compile(r'#EXTINF:-1.*,(.*)').search(line)
        if not match:
            continue

        lineObj = parse_extinf(line)

        name = lineObj["tvg-name"]
        channel_id = lineObj["tvg-id"]
        channel_number = lineObj["tvg-chno"]


        event_info = parse_event_from_name(name)
        if not event_info or not event_info["event_name"]:
            # channels.append({
            #     "id": channel_number,
            #     "channel_name": event_info["channel_name"] if event_info else name,
            #     "icon_url": event_info["icon_url"] if event_info else "",
            #     "program_start": None,
            #     "program_name": None,
            #     "description": None
            # })
            continue

        channels.append({
            "id": channel_number,
            "channel_name": event_info["channel_name"],
            "program_name": event_info["event_name"],
            "icon_url": event_info["icon_url"],
            "program_start": event_info["xmltv_start"],
            "description": name
        })

    return channels


def generate_xmltv(channels, output_file):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<tv generator-info-name="epgBuilder">\n')

        for channel in channels:
            channel_name_escaped = html.escape(channel["channel_name"])
            f.write(f'  <channel id="{channel["id"]}">\n')
            f.write(f'    <display-name>{channel_name_escaped}</display-name>\n')
            if channel["icon_url"]:
                f.write(f'    <icon src="{channel["icon_url"]}"/>\n')
            f.write('  </channel>\n')

        for channel in channels:
            if not channel["program_start"]:
                continue  # skip channels with no scheduled program

            start_time = datetime.strptime(channel["program_start"], "%Y%m%d%H%M%S %z")
            stop_time = start_time + timedelta(hours=3)
            stop_time_str = stop_time.strftime("%Y%m%d%H%M%S %z")
            
            # Escape special XML characters
            program_name_escaped = html.escape(channel["program_name"])
            description_escaped = html.escape(channel["description"])
            
            f.write(f'  <programme channel="{channel["id"]}" start="{channel["program_start"]}" stop="{stop_time_str}">\n')
            f.write(f'    <title>{program_name_escaped}</title>\n')
            f.write(f'    <desc>{description_escaped}</desc>\n')
            f.write('  </programme>\n')

        f.write('</tv>\n')




