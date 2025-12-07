from flask import Flask, redirect, Response, request, jsonify, stream_with_context
import json
from datetime import datetime
from dateutil import tz, parser as dtparse
import html
import requests

app = Flask(__name__)

def load_schedule():
    """Load the schedule from JSON file"""
    try:
        with open("/output/schedule.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        print("Error: Invalid JSON in schedule.json")
        return {}

def get_current_event(service_channel, current_time=None):
    """Find which event is currently live"""
    if current_time is None:
        current_time = datetime.now(tz.UTC)
    
    for program in service_channel["programs"]:
        start_dt = dtparse.isoparse(program["start_dt"])
        stop_dt = dtparse.isoparse(program["stop_dt"])
        
        if start_dt <= current_time < stop_dt:
            return program
    
    return None

def get_next_event(service_channel, current_time=None):
    """Find the next upcoming event"""
    if current_time is None:
        current_time = datetime.now(tz.UTC)
    
    for program in service_channel["programs"]:
        start_dt = dtparse.isoparse(program["start_dt"])
        
        if start_dt > current_time:
            return program
    
    return None

@app.route('/stream/<channel_id>')
@app.route('/stream/<channel_id>.m3u8')
def stream(channel_id):
    """Return M3U8 playlist pointing to the current live stream"""
    # Remove .m3u8 extension if present
    channel_id = channel_id.replace('.m3u8', '')
    
    schedule = load_schedule()
    
    # Find the combined channel across all services
    service_channel = None
    for pattern_name, pattern_data in schedule.items():
        for ch in pattern_data["service_channels"]:
            if ch["id"] == channel_id:
                service_channel = ch
                break
        if service_channel:
            break
    
    if not service_channel:
        print(f"[STREAM] Channel not found: {channel_id}")
        return Response("Channel not found", status=404, mimetype='text/plain')
    
    # Get current event
    current_event = get_current_event(service_channel)
    
    if not current_event or not current_event.get("stream_url"):
        # No current event - return empty playlist
        print(f"[STREAM] No event for channel: {channel_id}")
        next_event = get_next_event(service_channel)
        
        if next_event:
            start_dt = dtparse.isoparse(next_event["start_dt"])
            comment = f"# Next: {next_event['program_name']} at {start_dt.strftime('%Y-%m-%d %H:%M %Z')}"
        else:
            comment = "# No events scheduled"
        
        m3u8_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
{comment}
#EXT-X-ENDLIST
"""
        response = Response(m3u8_content, mimetype='application/vnd.apple.mpegurl')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    
    # Return M3U8 pointing to the actual stream
    stream_url = current_event["stream_url"]
    
    print(f"[STREAM] Channel: {channel_id}")
    print(f"[STREAM] Event: {current_event['program_name']}")
    print(f"[STREAM] URL: {stream_url}")
    
    # If the URL is already an M3U8, create a simple redirect playlist
    if '.m3u8' in stream_url or 'm3u8' in stream_url.lower():
        m3u8_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=5000000
{stream_url}
"""
    else:
        # For direct streams (TS, etc), create a simple playlist
        m3u8_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,{current_event['program_name']}
{stream_url}
#EXT-X-ENDLIST
"""
    
    response = Response(m3u8_content, mimetype='application/vnd.apple.mpegurl')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@app.route('/playlist.m3u')
def playlist():
    """Generate M3U playlist with dynamic stream URLs"""
    schedule = load_schedule()
    
    # Get base URL for this server
    base_url = request.url_root.rstrip('/')
    
    lines = ['#EXTM3U']
    
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            # Only include channels that have at least one program
            if len(channel["programs"]) > 0:
                extinf = f'#EXTINF:-1 tvg-id="{channel["id"]}" tvg-name="{channel["channel_name"]}" tvg-logo="{channel["icon_url"]}" group-title="{pattern_data["category"]}",{channel["channel_name"]}'
                # Use .m3u8 extension to indicate HLS stream
                stream_url = f'{base_url}/stream/{channel["id"]}.m3u8'
                
                lines.append(extinf)
                lines.append(stream_url)
    
    response = Response('\n'.join(lines), mimetype='audio/x-mpegurl')
    response.headers['Cache-Control'] = 'no-cache'
    
    return response

@app.route('/epg.xml')
def epg():
    """Generate XMLTV EPG for combined channels"""
    schedule = load_schedule()
    
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="epgBuilder-combined">']
    
    # Generate channel definitions
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            if len(channel["programs"]) > 0:
                channel_id_escaped = html.escape(str(channel["id"]), quote=True)
                channel_name_escaped = html.escape(str(channel["channel_name"]), quote=False)
                icon_url_escaped = html.escape(str(channel["icon_url"]), quote=True) if channel.get("icon_url") else ""
                
                lines.append(f'  <channel id="{channel_id_escaped}">')
                lines.append(f'    <display-name>{channel_name_escaped}</display-name>')
                if channel.get("icon_url"):
                    lines.append(f'    <icon src="{icon_url_escaped}"/>')
                lines.append('  </channel>')
    
    # Generate programme listings
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            channel_id_escaped = html.escape(str(channel["id"]), quote=True)
            
            for program in channel["programs"]:
                # Escape all program data
                program_name_escaped = html.escape(str(program.get("program_name", "")), quote=False)
                description_escaped = html.escape(str(program.get("description", "")), quote=False)
                start_str = program["start_str"]
                stop_str = program["stop_str"]
                
                lines.append(f'  <programme channel="{channel_id_escaped}" start="{start_str}" stop="{stop_str}">')
                lines.append(f'    <title>{program_name_escaped}</title>')
                lines.append(f'    <desc>{description_escaped}</desc>')
                
                if pattern_data.get("category"):
                    category_escaped = html.escape(str(pattern_data["category"]), quote=False)
                    lines.append(f'    <category lang="en">{category_escaped}</category>')
                
                if program.get("icon_url"):
                    icon_url_escaped = html.escape(str(program["icon_url"]), quote=True)
                    lines.append(f'    <icon src="{icon_url_escaped}"/>')
                
                lines.append('  </programme>')
    
    lines.append('</tv>')
    
    response = Response('\n'.join(lines), mimetype='application/xml')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@app.route('/schedule')
def schedule_json():
    """Return the full schedule as JSON"""
    schedule = load_schedule()
    return jsonify(schedule)

@app.route('/schedule/<channel_id>')
def channel_schedule(channel_id):
    """Get schedule for a specific combined channel"""
    schedule = load_schedule()
    
    # Find the channel
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            if channel["id"] == channel_id:
                current_event = get_current_event(channel)
                next_event = get_next_event(channel)
                
                return jsonify({
                    "channel": channel,
                    "current_event": current_event,
                    "next_event": next_event
                })
    
    return jsonify({"error": "Channel not found"}), 404

@app.route('/health')
def health():
    """Health check endpoint"""
    schedule = load_schedule()
    total_channels = sum(len(p["service_channels"]) for p in schedule.values())
    total_programs = sum(
        len(ch["programs"]) 
        for p in schedule.values() 
        for ch in p["service_channels"]
    )
    
    return jsonify({
        "status": "ok",
        "total_channels": total_channels,
        "total_programs": total_programs,
        "timestamp": datetime.now(tz.UTC).isoformat()
    })

@app.route('/')
def index():
    """Simple landing page with links"""
    schedule = load_schedule()
    total_channels = sum(len(p["service_channels"]) for p in schedule.values())
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>EPG Builder</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            h1 {{ color: #333; }}
            .link {{ margin: 10px 0; }}
            a {{ color: #0066cc; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <h1>EPG Builder - Combined Channels</h1>
        <p>Total Combined Channels: {total_channels}</p>
        <div class="link"><a href="/playlist.m3u">Download M3U Playlist</a></div>
        <div class="link"><a href="/epg.xml">Download XMLTV EPG</a></div>
        <div class="link"><a href="/schedule">View Schedule (JSON)</a></div>
        <div class="link"><a href="/health">Health Check</a></div>
    </body>
    </html>
    """
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)