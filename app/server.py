from flask import Flask, Response, request, jsonify, send_file
import json
from datetime import datetime
from dateutil import tz, parser as dtparse
import html
import os

app = Flask(__name__)

# Import stream manager
from stream_manager import stream_manager

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

@app.route('/stream/<channel_id>.m3u8')
@app.route('/stream/<channel_id>/stream.m3u8')
def stream(channel_id):
    """Serve HLS playlist for a channel - starts FFmpeg on-demand"""
    channel_id = channel_id.replace('.m3u8', '')
    
    # Get or start the stream
    playlist_path = stream_manager.get_stream_playlist(channel_id)
    
    if not playlist_path:
        return Response("Stream not available", status=404, mimetype='text/plain')
    
    # Wait a bit for FFmpeg to create the playlist
    import time
    for i in range(10):  # Wait up to 5 seconds
        if os.path.exists(playlist_path):
            break
        time.sleep(0.5)
    
    if not os.path.exists(playlist_path):
        return Response("Stream starting, please retry", status=503, mimetype='text/plain')
    
    try:
        with open(playlist_path, 'r') as f:
            content = f.read()
        
        # Rewrite segment URLs to include channel_id path
        # Replace relative segment paths with full URLs
        base_url = request.url_root.rstrip('/')
        lines = content.split('\n')
        rewritten_lines = []
        
        for line in lines:
            # If line is a segment filename (ends with .ts and doesn't start with #)
            if line.strip().endswith('.ts') and not line.startswith('#'):
                # Rewrite to full URL
                segment_name = line.strip()
                rewritten_lines.append(f'{base_url}/stream/{channel_id}/{segment_name}')
            else:
                rewritten_lines.append(line)
        
        rewritten_content = '\n'.join(rewritten_lines)
        
        # Update access time to prevent idle timeout
        stream_manager.update_access_time(channel_id)
        
        response = Response(rewritten_content, mimetype='application/vnd.apple.mpegurl')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        return response
    except Exception as e:
        print(f"[STREAM] Error serving playlist: {e}")
        return Response(f"Error: {e}", status=500, mimetype='text/plain')

@app.route('/stream/<channel_id>/<segment>')
def stream_segment(channel_id, segment):
    """Serve HLS segment"""
    segment_path = f"/output/hls/{channel_id}/{segment}"
    
    if not os.path.exists(segment_path):
        return Response("Segment not found", status=404, mimetype='text/plain')
    
    # Update access time
    stream_manager.update_access_time(channel_id)
    
    return send_file(segment_path, mimetype='video/mp2t')

@app.route('/playlist.m3u')
def playlist():
    """Generate M3U playlist with HLS stream URLs"""
    schedule = load_schedule()
    
    base_url = request.url_root.rstrip('/')
    
    lines = ['#EXTM3U']
    
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            if len(channel["programs"]) > 0:
                extinf = f'#EXTINF:-1 tvg-id="{channel["id"]}" tvg-name="{channel["channel_name"]}" tvg-logo="{channel["icon_url"]}" group-title="{pattern_data["category"]}",{channel["channel_name"]}'
                stream_url = f'{base_url}/stream/{channel["id"]}.m3u8'
                
                lines.append(extinf)
                lines.append(stream_url)
    
    response = Response('\n'.join(lines), mimetype='audio/x-mpegurl')
    response.headers['Cache-Control'] = 'no-cache'
    
    return response

@app.route('/streams/status')
def streams_status():
    """Get status of all active streams"""
    return jsonify(stream_manager.get_stream_status())

@app.route('/streams/stop/<channel_id>')
def stop_stream(channel_id):
    """Manually stop a stream"""
    stream_manager.stop_stream(channel_id)
    return jsonify({"status": "stopped", "channel_id": channel_id})

# ... keep all existing routes (epg, schedule, health, index) ...

@app.route('/epg.xml')
def epg():
    """Generate XMLTV EPG for combined channels"""
    schedule = load_schedule()
    
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="epgBuilder-combined">']
    
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
    
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            channel_id_escaped = html.escape(str(channel["id"]), quote=True)
            
            for program in channel["programs"]:
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
    
    return response

@app.route('/health')
def health():
    """Health check endpoint"""
    schedule = load_schedule()
    status = stream_manager.get_stream_status()
    
    return jsonify({
        "status": "ok",
        "total_channels": sum(len(p["service_channels"]) for p in schedule.values()),
        "active_streams": len(status),
        "timestamp": datetime.now(tz.UTC).isoformat()
    })

@app.route('/')
def index():
    """Landing page"""
    schedule = load_schedule()
    status = stream_manager.get_stream_status()
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>EPG Builder</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>EPG Builder - Combined Channels</h1>
        <p>Active Streams: {len(status)}</p>
        <div><a href="/playlist.m3u">Download M3U Playlist</a></div>
        <div><a href="/epg.xml">Download XMLTV EPG</a></div>
        <div><a href="/streams/status">Active Streams Status</a></div>
        <div><a href="/health">Health Check</a></div>
    </body>
    </html>
    """
    return html_content

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=os.getenv("SERVER_PORT", 8080), debug=False, threaded=True)