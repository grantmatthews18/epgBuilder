from flask import Flask, Response, request, jsonify, send_file, stream_with_context
import json
from datetime import datetime
from dateutil import tz, parser as dtparse
import html
import os
import requests

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
    

def stream_ts(url):
    """Stream TS content from upstream URL - optimized for live streams"""
    try:
        # Headers that mimic a real IPTV player
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Connection': 'keep-alive',
            'Accept-Encoding': 'identity',  # Don't compress
        }
        
        app.logger.info(f"[STREAM] Connecting to {url}")
        
        # Open connection with longer timeout for live streams
        r = requests.get(
            url, 
            stream=True, 
            timeout=(10, 60),  # (connect timeout, read timeout)
            headers=headers,
            allow_redirects=True
        )
        
        # Check status BEFORE yielding any data
        if r.status_code != 200:
            app.logger.error(f"Upstream error {r.status_code} for URL: {url}")
            app.logger.error(f"Response headers: {dict(r.headers)}")
            r.close()
            return
        
        app.logger.info(f"[STREAM] Connected successfully, content-type: {r.headers.get('Content-Type')}")
        
        # Stream the content in chunks
        chunk_count = 0
        for chunk in r.iter_content(chunk_size=32768):  # Larger chunks for video
            if chunk:
                chunk_count += 1
                if chunk_count == 1:
                    app.logger.info(f"[STREAM] First chunk received, size: {len(chunk)} bytes")
                if chunk_count % 100 == 0:
                    app.logger.debug(f"[STREAM] Streamed {chunk_count} chunks (~{chunk_count * 32 // 1024}MB)")
                yield chunk
        
        app.logger.info(f"[STREAM] Stream ended normally, {chunk_count} chunks sent")
                
    except requests.exceptions.Timeout as e:
        app.logger.error(f"Stream timeout for {url}: {e}")
        return
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Stream error for {url}: {e}")
        return
    except GeneratorExit:
        app.logger.info(f"[STREAM] Client disconnected")
        return
    except Exception as e:
        app.logger.error(f"Unexpected error streaming {url}: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return


@app.route('/stream/<channel_id>')
@app.route('/stream/<channel_id>.ts')
def stream(channel_id):
    channel_id = channel_id.replace('.ts', '')

    schedule = load_schedule()
    
    channel = None
    for pattern_name, pattern_data in schedule.items():
        for service_channel in pattern_data.get("service_channels", []):
            if service_channel["id"] == channel_id:
                channel = service_channel
                break
        if channel:
            break

    if not channel:
        app.logger.error(f"Channel ID {channel_id} not found in schedule")
        return Response("Channel not found", status=404, mimetype='text/plain')
    
    # Find which event is currently live
    now = datetime.now(tz.UTC)
    
    event = None
    for program in channel["programs"]:
        start_dt_str = program.get("start_dt")
        stop_dt_str = program.get("stop_dt")

        if not start_dt_str or not stop_dt_str:
            continue
        
        start_dt = dtparse.isoparse(start_dt_str)
        stop_dt = dtparse.isoparse(stop_dt_str)
        
        if start_dt <= now < stop_dt:
            event = program
            break

    if not event or not event.get("stream_url"):
        app.logger.error(f"No active event for channel ID {channel_id}")
        app.logger.error(f"Current time: {now.isoformat()}")
        return Response("No active event for this channel", status=404, mimetype='text/plain')
    
    stream_url = event["stream_url"]
    
    app.logger.info(f"[STREAM] Request for channel: {channel_id}, Event: {event['program_name']}")

    # Stream with proper headers for live video
    return Response(
        stream_with_context(stream_ts(stream_url)),
        mimetype='video/mp2t',  # Proper MIME type for MPEG-TS
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Connection': 'keep-alive',
            'Accept-Ranges': 'none',  # Live stream, no seeking
        }
    )

@app.route('/playlist.m3u')
def playlist():
    """Generate M3U playlist with direct stream URLs"""
    schedule = load_schedule()
    
    base_url = request.url_root.rstrip('/')
    
    lines = ['#EXTM3U']
    
    for pattern_name, pattern_data in schedule.items():
        for channel in pattern_data["service_channels"]:
            if len(channel["programs"]) > 0:
                extinf = f'#EXTINF:-1 tvg-id="{channel["id"]}" tvg-name="{channel["channel_name"]}" tvg-logo="{channel["icon_url"]}" group-title="{pattern_data["category"]}",{channel["channel_name"]}'
                stream_url = f'{base_url}/stream/{channel["id"]}.ts'
                
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
    app.run(host='0.0.0.0', port=os.getenv("SERVER_PORT", 8080), debug=os.getenv("DEBUG", False), threaded=True)