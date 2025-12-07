# Proxy-Based Stream Architecture

## Overview
The EPG Builder now uses a **simple HTTP proxy approach** to stream content from provider channels to clients. This eliminates the need for FFmpeg and provides seamless automatic switching between provider streams as events change.

## How It Works

### 1. Stream Request Flow
```
Client → /stream/{channel_id}.ts → Proxy → Provider Stream → Client
```

When a client requests a combined channel stream:
1. Client makes HTTP request to `http://server:8080/stream/nhl_ppv_1.ts`
2. Server looks up what event is currently live on that combined channel
3. Server proxies the provider stream URL directly to the client
4. Server monitors for event changes every 5 seconds

### 2. Automatic Stream Switching

The proxy continuously monitors the schedule:
- **Every 5 seconds**: Check if the current event has changed
- **When event changes**: 
  - Close connection to old provider stream
  - Open connection to new provider stream
  - Continue streaming to client without interruption

### 3. Key Components

#### StreamManager (`app/stream_manager.py`)
- **`proxy_stream(channel_id)`**: Generator function that yields stream chunks
- **`_monitor_stream(channel_id)`**: Background thread checking for event changes
- **`get_current_stream_url(channel_id)`**: Determines which provider stream should be active

#### Server Routes (`app/server.py`)
- **`/stream/{channel_id}.ts`**: Main streaming endpoint (direct proxy)
- **`/playlist.m3u`**: M3U playlist with combined channel URLs
- **`/epg.xml`**: XMLTV EPG with combined channel schedules
- **`/streams/status`**: View active proxy streams

## Advantages Over FFmpeg Approach

### ✅ Simpler
- No FFmpeg processes to manage
- No HLS segments to generate/serve
- No file I/O for playlists/segments

### ✅ Lower Resource Usage
- Pure Python HTTP proxying
- No video encoding/transcoding
- Minimal CPU usage

### ✅ More Reliable
- No FFmpeg crashes
- No segment synchronization issues
- Direct pass-through of provider stream

### ✅ Faster Switching
- No FFmpeg restart needed
- Switch happens in <1 second
- No buffer interruptions

## Technical Details

### Idle Timeout
Streams automatically stop after **5 minutes** of no client activity to save resources:
```python
self.idle_timeout = 300  # 5 minutes
```

### Chunk Size
Data is proxied in **8KB chunks** for optimal streaming performance:
```python
for chunk in response.iter_content(chunk_size=8192):
```

### Session Management
Each proxy uses a persistent HTTP session with custom headers:
```python
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})
```

## Example Usage

### M3U Playlist URL
```
http://nordvpn:8080/playlist.m3u
```

### Direct Stream URL
```
http://nordvpn:8080/stream/nhl_ppv_1.ts
```

### Check Active Streams
```bash
curl http://nordvpn:8080/streams/status
```

### Stop a Stream Manually
```bash
curl http://nordvpn:8080/streams/stop/nhl_ppv_1
```

## Monitoring

### View Logs
```bash
docker logs -f epgbuilder
```

Look for proxy activity:
```
[PROXY] Starting proxy for nhl_ppv_1 -> http://provider.com/stream1.ts
[PROXY] nhl_ppv_1 switching from stream1.ts to stream2.ts
[PROXY] nhl_ppv_1 idle for 300s, stopping
```

### Stream Status Endpoint
```bash
curl http://localhost:8080/streams/status
```

Response:
```json
{
  "nhl_ppv_1": {
    "stream_url": "http://provider.com/channel123.ts",
    "uptime_seconds": 1234,
    "idle_seconds": 5,
    "last_check": 1234567890.123
  }
}
```

## Limitations

### Client Compatibility
- Works with any client that supports HTTP streaming (most IPTV apps)
- No special HLS/M3U8 support required
- Direct TS stream works with Plex, VLC, xTeVe, etc.

### Network Considerations
- Bandwidth: Server bandwidth = Provider bandwidth + Client bandwidth
- Latency: Minimal added latency (just HTTP proxy overhead)
- Concurrent streams: Limited only by server resources

## Troubleshooting

### Stream Not Starting
1. Check if event is currently scheduled:
   ```bash
   curl http://localhost:8080/schedule.json | jq
   ```

2. Verify provider stream URL is accessible:
   ```bash
   curl -I http://provider.com/stream.ts
   ```

### Stream Switching Not Working
1. Check monitor thread logs for errors
2. Verify schedule.json has correct event times
3. Ensure provider stream URLs are valid

### High CPU/Memory Usage
- Check number of active streams: `curl http://localhost:8080/streams/status`
- Reduce idle timeout if needed
- Monitor for stuck streams

## Configuration

### Adjust Idle Timeout
Edit `app/stream_manager.py`:
```python
self.idle_timeout = 300  # Change to desired seconds
```

### Adjust Monitor Interval
Edit `app/stream_manager.py`:
```python
check_interval = 5  # Change to desired seconds
```

### Adjust Chunk Size
Edit `app/stream_manager.py`:
```python
for chunk in response.iter_content(chunk_size=8192):  # Change size
```

## Migration from FFmpeg

The old FFmpeg-based implementation has been saved to:
```
app/stream_manager_old.py
```

To roll back if needed:
```bash
mv app/stream_manager.py app/stream_manager_proxy.py
mv app/stream_manager_old.py app/stream_manager.py
docker compose restart
```
