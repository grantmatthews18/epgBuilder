# from flask import Flask, Response, request, jsonify, stream_with_context
# import json
# from datetime import datetime
# from dateutil import tz, parser as dtparse
# import html
# import os
# import requests

# app = Flask(__name__)

# def load_schedule():
#     """Load the schedule from JSON file"""
#     try:
#         with open("/output/schedule.json", "r") as f:
#             return json.load(f)
#     except FileNotFoundError:
#         return {}
#     except json.JSONDecodeError:
#         print("Error: Invalid JSON in schedule.json")
#         return {}
    

# def stream_ts(url):
#     """Stream TS content from upstream URL - optimized for live streams"""
#     session = None
#     bytes_sent = 0
#     chunk_count = 0
#     try:
#         # Headers that mimic a real IPTV player
#         headers = {
#             'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
#             'Accept': '*/*',
#             'Connection': 'keep-alive',
#             'Accept-Encoding': 'identity',
#         }
        
#         app.logger.info(f"[STREAM] Connecting to {url}")
        
#         # Use a session for better connection reuse
#         session = requests.Session()
        
#         # Open connection with longer timeout for live streams
#         r = session.get(
#             url, 
#             stream=True, 
#             timeout=(10, None),  # Connect timeout 10s, no read timeout for live streams
#             headers=headers,
#             allow_redirects=True
#         )
        
#         # Check status BEFORE yielding any data
#         if r.status_code != 200:
#             app.logger.error(f"Upstream error {r.status_code} for URL: {url}")
#             app.logger.error(f"Response headers: {dict(r.headers)}")
#             r.close()
#             return
        
#         app.logger.info(f"[STREAM] Connected successfully, content-type: {r.headers.get('Content-Type')}")
        
#         # Stream the content in small chunks for low latency
#         for chunk in r.iter_content(chunk_size=8192):
#             if chunk:
#                 chunk_count += 1
#                 bytes_sent += len(chunk)
                
#                 if chunk_count == 1:
#                     app.logger.info(f"[STREAM] First chunk received, size: {len(chunk)} bytes")
                
#                 if chunk_count % 500 == 0:
#                     mb_sent = bytes_sent / (1024 * 1024)
#                     app.logger.info(f"[STREAM] Streamed {chunk_count} chunks ({mb_sent:.2f}MB)")
                
#                 yield chunk
        
#         app.logger.info(f"[STREAM] Stream ended normally, sent {bytes_sent / (1024 * 1024):.2f}MB in {chunk_count} chunks")
                
#     except requests.exceptions.Timeout as e:
#         app.logger.error(f"Stream timeout for {url}: {e}")
#     except requests.exceptions.RequestException as e:
#         app.logger.error(f"Stream error for {url}: {e}")
#     except GeneratorExit:
#         mb_sent = bytes_sent / (1024 * 1024)
#         app.logger.info(f"[STREAM] Client disconnected after {mb_sent:.2f}MB ({chunk_count} chunks)")
#     except Exception as e:
#         app.logger.error(f"Unexpected error streaming {url}: {e}")
#         import traceback
#         app.logger.error(traceback.format_exc())
#     finally:
#         if session:
#             session.close()


# @app.route('/stream/<channel_id>')
# @app.route('/stream/<channel_id>.ts')
# def stream(channel_id):
#     channel_id = channel_id.replace('.ts', '')

#     schedule = load_schedule()
    
#     channel = None
#     for pattern_name, pattern_data in schedule.items():
#         for service_channel in pattern_data.get("service_channels", []):
#             if service_channel["id"] == channel_id:
#                 channel = service_channel
#                 break
#         if channel:
#             break

#     if not channel:
#         app.logger.error(f"Channel ID {channel_id} not found in schedule")
#         return Response("Channel not found", status=404, mimetype='text/plain')
    
#     # Find which event is currently live
#     now = datetime.now(tz.UTC)
    
#     event = None
#     for program in channel["programs"]:
#         start_dt_str = program.get("start_dt")
#         stop_dt_str = program.get("stop_dt")

#         if not start_dt_str or not stop_dt_str:
#             continue
        
#         start_dt = dtparse.isoparse(start_dt_str)
#         stop_dt = dtparse.isoparse(stop_dt_str)
        
#         if start_dt <= now < stop_dt:
#             event = program
#             break

#     if not event or not event.get("stream_url"):
#         app.logger.error(f"No active event for channel ID {channel_id}")
#         app.logger.error(f"Current time: {now.isoformat()}")
#         return Response("No active event for this channel", status=404, mimetype='text/plain')
    
#     stream_url = event["stream_url"]
    
#     app.logger.info(f"[STREAM] Request for channel: {channel_id}, Event: {event['program_name']}, Client: {request.remote_addr}")

#     # Stream with proper headers for live video
#     response = Response(
#         stream_with_context(stream_ts(stream_url)),
#         mimetype='video/mp2t',
#         headers={
#             'Cache-Control': 'no-cache, no-store, must-revalidate',
#             'Pragma': 'no-cache',
#             'Expires': '0',
#             'X-Content-Type-Options': 'nosniff',
#             'Accept-Ranges': 'none',
#         }
#     )
    
#     # Don't set Connection header - let Flask/Werkzeug handle it
#     return response

# @app.route('/playlist.m3u')
# def playlist():
#     """Generate M3U playlist with direct stream URLs"""
#     schedule = load_schedule()
    
#     base_url = request.url_root.rstrip('/')
    
#     lines = ['#EXTM3U']
    
#     for pattern_name, pattern_data in schedule.items():
#         for channel in pattern_data["service_channels"]:
#             if len(channel["programs"]) > 0:
#                 extinf = f'#EXTINF:-1 tvg-id="{channel["id"]}" tvg-name="{channel["channel_name"]}" tvg-logo="{channel["icon_url"]}" group-title="{pattern_data["category"]}",{channel["channel_name"]}'
#                 stream_url = f'{base_url}/stream/{channel["id"]}.ts'
                
#                 lines.append(extinf)
#                 lines.append(stream_url)
    
#     response = Response('\n'.join(lines), mimetype='audio/x-mpegurl')
#     response.headers['Cache-Control'] = 'no-cache'
    
#     return response

# @app.route('/epg.xml')
# def epg():
#     """Generate XMLTV EPG for combined channels"""
#     schedule = load_schedule()
    
#     lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="epgBuilder-combined">']
    
#     for pattern_name, pattern_data in schedule.items():
#         for channel in pattern_data["service_channels"]:
#             if len(channel["programs"]) > 0:
#                 channel_id_escaped = html.escape(str(channel["id"]), quote=True)
#                 channel_name_escaped = html.escape(str(channel["channel_name"]), quote=False)
#                 icon_url_escaped = html.escape(str(channel["icon_url"]), quote=True) if channel.get("icon_url") else ""
                
#                 lines.append(f'  <channel id="{channel_id_escaped}">')
#                 lines.append(f'    <display-name>{channel_name_escaped}</display-name>')
#                 if channel.get("icon_url"):
#                     lines.append(f'    <icon src="{icon_url_escaped}"/>')
#                 lines.append('  </channel>')
    
#     for pattern_name, pattern_data in schedule.items():
#         for channel in pattern_data["service_channels"]:
#             channel_id_escaped = html.escape(str(channel["id"]), quote=True)
            
#             for program in channel["programs"]:
#                 program_name_escaped = html.escape(str(program.get("program_name", "")), quote=False)
#                 description_escaped = html.escape(str(program.get("description", "")), quote=False)
#                 start_str = program["start_str"]
#                 stop_str = program["stop_str"]
                
#                 lines.append(f'  <programme channel="{channel_id_escaped}" start="{start_str}" stop="{stop_str}">')
#                 lines.append(f'    <title>{program_name_escaped}</title>')
#                 lines.append(f'    <desc>{description_escaped}</desc>')
                
#                 if pattern_data.get("category"):
#                     category_escaped = html.escape(str(pattern_data["category"]), quote=False)
#                     lines.append(f'    <category lang="en">{category_escaped}</category>')
                
#                 if program.get("icon_url"):
#                     icon_url_escaped = html.escape(str(program["icon_url"]), quote=True)
#                     lines.append(f'    <icon src="{icon_url_escaped}"/>')
                
#                 lines.append('  </programme>')
    
#     lines.append('</tv>')
    
#     response = Response('\n'.join(lines), mimetype='application/xml')
#     response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
#     return response

# @app.route('/health')
# def health():
#     """Health check endpoint"""
#     schedule = load_schedule()
    
#     return jsonify({
#         "status": "ok",
#         "total_channels": sum(len(p["service_channels"]) for p in schedule.values()),
#         "timestamp": datetime.now(tz.UTC).isoformat()
#     })

# @app.route('/')
# def index():
#     """Landing page"""
#     schedule = load_schedule()
    
#     html_content = f"""
#     <!DOCTYPE html>
#     <html>
#     <head><title>EPG Builder</title></head>
#     <body style="font-family: Arial; margin: 40px;">
#         <h1>EPG Builder - Combined Channels</h1>
#         <div><a href="/playlist.m3u">Download M3U Playlist</a></div>
#         <div><a href="/epg.xml">Download XMLTV EPG</a></div>
#         <div><a href="/health">Health Check</a></div>
#     </body>
#     </html>
#     """
#     return html_content

# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=int(os.getenv("SERVER_PORT", 8080)), debug=os.getenv("DEBUG", False), threaded=True)

from flask import Flask, Response, request, jsonify
import json
from datetime import datetime
from dateutil import tz, parser as dtparse
import html
import os
import requests
import urllib3

# Disable SSL warnings if needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Disable all Flask response buffering
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

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
    """Stream TS content with minimal buffering to avoid discontinuities"""
    bytes_sent = 0
    chunk_count = 0
    r = None
    
    try:
        headers = {
            'User-Agent': 'VLC/3.0.18 LibVLC/3.0.18',
            'Accept': '*/*',
        }
        
        app.logger.info(f"[STREAM] Connecting to {url}")
        
        r = requests.get(
            url,
            stream=True,
            timeout=(15, None),
            headers=headers,
            allow_redirects=True,
            verify=False
        )
        
        if r.status_code != 200:
            app.logger.error(f"Upstream error {r.status_code}")
            if r is not None:
                r.close()
            return
        
        app.logger.info(f"[STREAM] Connected, streaming...")
        
        # Log upstream headers for debugging
        app.logger.info(f"[STREAM] Upstream Content-Type: {r.headers.get('Content-Type')}")
        app.logger.info(f"[STREAM] Upstream Content-Length: {r.headers.get('Content-Length', 'N/A')}")
        app.logger.info(f"[STREAM] Upstream Accept-Ranges: {r.headers.get('Accept-Ranges', 'N/A')}")
        
        TS_PACKET_SIZE = 188
        CHUNK_SIZE = TS_PACKET_SIZE * 80
        
        for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            
            chunk_count += 1
            bytes_sent += len(chunk)
            
            if chunk_count == 1:
                app.logger.info(f"[STREAM] First chunk: {len(chunk)} bytes")
            
            if chunk_count % 2000 == 0:
                mb = bytes_sent / (1024 * 1024)
                app.logger.info(f"[STREAM] {mb:.1f}MB sent")
            
            yield chunk
        
        app.logger.info(f"[STREAM] Stream ended: {bytes_sent / (1024 * 1024):.2f}MB")
                
    except requests.exceptions.Timeout:
        app.logger.error(f"[STREAM] Connection timeout")
    except requests.exceptions.ConnectionError as e:
        app.logger.error(f"[STREAM] Connection error: {e}")
    except GeneratorExit:
        mb = bytes_sent / (1024 * 1024) if bytes_sent > 0 else 0
        app.logger.info(f"[STREAM] Client disconnected after {mb:.2f}MB")
    except Exception as e:
        app.logger.error(f"[STREAM] Unexpected error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
    finally:
        if r is not None:
            try:
                r.close()
            except:
                pass
            

def proxy_with_range(url, range_header):
    """Proxy request with Range header support"""
    try:
        headers = {
            'User-Agent': 'VLC/3.0.18 LibVLC/3.0.18',
            'Accept': '*/*',
            'Range': range_header
        }
        
        app.logger.info(f"[RANGE] Forwarding range request: {range_header}")
        
        r = requests.get(
            url,
            stream=True,
            timeout=(15, None),
            headers=headers,
            allow_redirects=True,
            verify=False
        )
        
        app.logger.info(f"[RANGE] Upstream status: {r.status_code}")
        app.logger.info(f"[RANGE] Upstream Content-Range: {r.headers.get('Content-Range', 'N/A')}")
        app.logger.info(f"[RANGE] Upstream Content-Length: {r.headers.get('Content-Length', 'N/A')}")
        
        # If upstream doesn't support range, return 200 with full content
        status = r.status_code if r.status_code in [200, 206] else 200
        
        chunk_count = 0
        bytes_sent = 0
        
        def generate():
            nonlocal chunk_count, bytes_sent
            try:
                for chunk in r.iter_content(chunk_size=16384):
                    if chunk:
                        chunk_count += 1
                        bytes_sent += len(chunk)
                        
                        if chunk_count == 1:
                            app.logger.info(f"[RANGE] First chunk sent: {len(chunk)} bytes")
                        
                        if chunk_count % 1000 == 0:
                            mb = bytes_sent / (1024 * 1024)
                            app.logger.info(f"[RANGE] Sent {mb:.1f}MB")
                        
                        yield chunk
                
                app.logger.info(f"[RANGE] Stream completed: {bytes_sent / (1024 * 1024):.2f}MB")
            except GeneratorExit:
                app.logger.info(f"[RANGE] Client disconnected after {bytes_sent / (1024 * 1024):.2f}MB")
            finally:
                r.close()
        
        response_headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*',
        }
        
        # Only include Content-Range if upstream provided it
        if r.headers.get('Content-Range'):
            response_headers['Content-Range'] = r.headers['Content-Range']
        
        # Only include Content-Length if upstream provided it
        if r.headers.get('Content-Length'):
            response_headers['Content-Length'] = r.headers['Content-Length']
        
        response = Response(
            generate(),
            status=status,
            mimetype='video/mp2t',
            headers=response_headers
        )
        
        return response
        
    except Exception as e:
        app.logger.error(f"[RANGE] Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return Response("Range request failed", status=500)


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
        app.logger.error(f"Channel {channel_id} not found")
        return Response("Channel not found", status=404, mimetype='text/plain')
    
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
        app.logger.error(f"No active event for {channel_id}")
        return Response("No active event", status=404, mimetype='text/plain')
    
    stream_url = event["stream_url"]
    
    app.logger.info(f"[STREAM] {channel_id} -> {event['program_name']} (Client: {request.remote_addr})")
    
    # Check if client sent Range header
    range_header = request.headers.get('Range')
    if range_header:
        app.logger.info(f"[STREAM] Client requested range: {range_header}")
        # Forward the range request to upstream
        return proxy_with_range(stream_url, range_header)
    
    # Normal streaming without range
    response = Response(
        stream_ts(stream_url),
        mimetype='video/mp2t',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Accept-Ranges': 'bytes',  # Changed from 'none' to 'bytes'
            'Access-Control-Allow-Origin': '*',
        }
    )
    
    return response


@app.route('/playlist.m3u')
def playlist():
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
    
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl')


@app.route('/epg.xml')
def epg():
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
    
    return Response('\n'.join(lines), mimetype='application/xml')


@app.route('/health')
def health():
    schedule = load_schedule()
    return jsonify({
        "status": "ok",
        "total_channels": sum(len(p["service_channels"]) for p in schedule.values()),
        "timestamp": datetime.now(tz.UTC).isoformat()
    })


@app.route('/')
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>EPG Builder</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>EPG Builder - Combined Channels</h1>
        <div><a href="/playlist.m3u">M3U Playlist</a></div>
        <div><a href="/epg.xml">XMLTV EPG</a></div>
        <div><a href="/health">Health</a></div>
    </body>
    </html>
    """


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv("SERVER_PORT", 8080)),
        debug=os.getenv("DEBUG", "False").lower() == "true",
        threaded=True,
        use_reloader=False
    )