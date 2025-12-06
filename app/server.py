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

@app.route('/stream/<channel_id>.m3u8')
def stream(channel_id):
    """Proxy the stream from provider to client"""
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
        return Response("Channel not found", status=404, mimetype='text/plain')
    
    # Get current event
    current_event = get_current_event(service_channel)
    
    if not current_event or not current_event.get("stream_url"):
        # No current event
        next_event = get_next_event(service_channel)
        if next_event:
            start_dt = dtparse.isoparse(next_event["start_dt"])
            error_msg = f"No event currently streaming. Next: {next_event['program_name']} at {start_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        else:
            error_msg = "No events scheduled for this channel"
        
        return Response(error_msg, status=503, mimetype='text/plain')
    
    # Proxy the stream
    stream_url = current_event["stream_url"]
    
    try:
        # Make a streaming request to the original URL
        def generate():
            with requests.get(stream_url, stream=True, timeout=30, headers={
                'User-Agent': request.headers.get('User-Agent', 'epgBuilder/1.0')
            }) as upstream:
                # Check if request was successful
                if upstream.status_code != 200:
                    print(f"Error fetching stream: {upstream.status_code}")
                    return
                
                # Stream the content in chunks
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        
        # Get content type from the original stream
        head = requests.head(stream_url, timeout=5, allow_redirects=True)
        content_type = head.headers.get('Content-Type', 'application/vnd.apple.mpegurl')
        
        response = Response(
            stream_with_context(generate()),
            mimetype=content_type,
            direct_passthrough=True
        )
        
        # Add headers to prevent caching
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['Connection'] = 'keep-alive'
        
        return response
        
    except requests.exceptions.Timeout:
        return Response("Stream timeout", status=504, mimetype='text/plain')
    except requests.exceptions.RequestException as e:
        print(f"Error proxying stream for {channel_id}: {e}")
        return Response(f"Stream error: {str(e)}", status=502, mimetype='text/plain')

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
                stream_url = f'{base_url}/stream/{channel["id"]}'
                
                lines.append(extinf)
                lines.append(stream_url)
    
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl')

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