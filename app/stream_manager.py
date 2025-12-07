import subprocess
import threading
import os
import time
from datetime import datetime, timedelta
from dateutil import tz, parser as dtparse
import json
import signal

class StreamManager:
    def __init__(self):
        self.active_streams = {}  # channel_id -> stream info
        self.stream_locks = {}  # channel_id -> threading.Lock
        self.output_dir = "/output/hls"
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Cleanup old HLS directories on startup
        self.cleanup_old_streams()
    
    def cleanup_old_streams(self):
        """Remove old HLS directories"""
        try:
            for item in os.listdir(self.output_dir):
                item_path = os.path.join(self.output_dir, item)
                if os.path.isdir(item_path):
                    import shutil
                    shutil.rmtree(item_path)
                    print(f"[CLEANUP] Removed old stream directory: {item}")
        except Exception as e:
            print(f"[CLEANUP] Error: {e}")
    
    def load_schedule(self):
        try:
            with open("/output/schedule.json", "r") as f:
                return json.load(f)
        except:
            return {}
    
    def get_current_event(self, service_channel):
        """Find which event is currently live"""
        now = datetime.now(tz.UTC)
        
        for program in service_channel["programs"]:
            start_dt = dtparse.isoparse(program["start_dt"])
            stop_dt = dtparse.isoparse(program["stop_dt"])
            
            if start_dt <= now < stop_dt:
                return program
        
        return None
    
    def get_lock(self, channel_id):
        """Get or create lock for a channel"""
        if channel_id not in self.stream_locks:
            self.stream_locks[channel_id] = threading.Lock()
        return self.stream_locks[channel_id]
    
    def start_stream(self, channel_id, stream_url, event_name):
        """Start FFmpeg process for a channel"""
        lock = self.get_lock(channel_id)
        
        with lock:
            # Check if already streaming this URL
            if channel_id in self.active_streams:
                active = self.active_streams[channel_id]
                if active['stream_url'] == stream_url and active['process'].poll() is None:
                    # Already streaming the correct URL and process is alive
                    active['last_accessed'] = datetime.now(tz.UTC)
                    print(f"[STREAM] Already streaming {channel_id}")
                    return active['playlist_path']
                else:
                    # Different URL or dead process, stop it
                    print(f"[STREAM] Stopping old stream for {channel_id}")
                    self._stop_stream_locked(channel_id)
            
            output_path = f"{self.output_dir}/{channel_id}"
            os.makedirs(output_path, exist_ok=True)
            
            playlist_path = f"{output_path}/stream.m3u8"
            
            # FFmpeg command - remux (copy codecs) for efficiency
            ffmpeg_cmd = [
                'ffmpeg',
                '-re',  # Read input at native frame rate
                '-i', stream_url,
                '-c', 'copy',  # Copy codecs (no transcoding)
                '-f', 'hls',
                '-hls_time', '6',
                '-hls_list_size', '10',
                '-hls_flags', 'delete_segments+append_list',
                '-hls_segment_filename', f'{output_path}/segment_%03d.ts',
                '-loglevel', 'warning',
                playlist_path
            ]
            
            print(f"[STREAM] Starting FFmpeg for {channel_id}")
            print(f"[STREAM] Event: {event_name}")
            print(f"[STREAM] Source: {stream_url}")
            
            try:
                process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    preexec_fn=os.setsid  # Create new process group
                )
                
                self.active_streams[channel_id] = {
                    'process': process,
                    'stream_url': stream_url,
                    'event_name': event_name,
                    'started_at': datetime.now(tz.UTC),
                    'last_accessed': datetime.now(tz.UTC),
                    'playlist_path': playlist_path
                }
                
                # Start monitoring thread for this stream
                monitor_thread = threading.Thread(
                    target=self._monitor_stream,
                    args=(channel_id,),
                    daemon=True
                )
                monitor_thread.start()
                
                print(f"[STREAM] FFmpeg started (PID: {process.pid})")
                return playlist_path
                
            except Exception as e:
                print(f"[STREAM] Error starting FFmpeg: {e}")
                return None
    
    def _monitor_stream(self, channel_id):
        """Monitor a specific stream - check for event changes and idle timeout"""
        while channel_id in self.active_streams:
            try:
                time.sleep(10)  # Check every 10 seconds
                
                lock = self.get_lock(channel_id)
                with lock:
                    if channel_id not in self.active_streams:
                        break
                    
                    stream_info = self.active_streams[channel_id]
                    process = stream_info['process']
                    
                    # Check if process died
                    if process.poll() is not None:
                        print(f"[MONITOR] FFmpeg died for {channel_id}")
                        self._stop_stream_locked(channel_id)
                        break
                    
                    # Check for idle timeout (no access in 5 minutes)
                    idle_time = datetime.now(tz.UTC) - stream_info['last_accessed']
                    if idle_time > timedelta(minutes=5):
                        print(f"[MONITOR] Stream {channel_id} idle for {idle_time}, stopping")
                        self._stop_stream_locked(channel_id)
                        break
                    
                    # Check if event changed
                    schedule = self.load_schedule()
                    service_channel = None
                    
                    for pattern_name, pattern_data in schedule.items():
                        for ch in pattern_data["service_channels"]:
                            if ch["id"] == channel_id:
                                service_channel = ch
                                break
                        if service_channel:
                            break
                    
                    if service_channel:
                        current_event = self.get_current_event(service_channel)
                        
                        if not current_event or not current_event.get("stream_url"):
                            # Event ended
                            print(f"[MONITOR] Event ended for {channel_id}, stopping")
                            self._stop_stream_locked(channel_id)
                            break
                        
                        if current_event['stream_url'] != stream_info['stream_url']:
                            # Event changed
                            print(f"[MONITOR] Event changed for {channel_id}, will restart on next request")
                            self._stop_stream_locked(channel_id)
                            break
                
            except Exception as e:
                print(f"[MONITOR] Error monitoring {channel_id}: {e}")
                time.sleep(30)
    
    def _stop_stream_locked(self, channel_id):
        """Stop stream (must be called with lock held)"""
        if channel_id not in self.active_streams:
            return
        
        stream_info = self.active_streams[channel_id]
        process = stream_info['process']
        
        print(f"[STREAM] Stopping {channel_id}")
        
        try:
            # Send SIGTERM to process group
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            
            # Wait up to 5 seconds
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
            
            print(f"[STREAM] Stopped {channel_id}")
            
        except Exception as e:
            print(f"[STREAM] Error stopping {channel_id}: {e}")
        
        finally:
            del self.active_streams[channel_id]
    
    def stop_stream(self, channel_id):
        """Stop stream (public method with locking)"""
        lock = self.get_lock(channel_id)
        with lock:
            self._stop_stream_locked(channel_id)
    
    def get_stream_playlist(self, channel_id):
        """Get the playlist path for a channel, starting stream if needed"""
        schedule = self.load_schedule()
        
        # Find the channel
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
            return None
        
        # Get current event
        current_event = self.get_current_event(service_channel)
        
        if not current_event or not current_event.get("stream_url"):
            print(f"[STREAM] No event for {channel_id}")
            return None
        
        # Start or reuse stream
        return self.start_stream(
            channel_id,
            current_event['stream_url'],
            current_event['program_name']
        )
    
    def update_access_time(self, channel_id):
        """Update last accessed time for a channel"""
        if channel_id in self.active_streams:
            self.active_streams[channel_id]['last_accessed'] = datetime.now(tz.UTC)
    
    def get_stream_status(self):
        """Get status of all active streams"""
        status = {}
        for channel_id, stream_info in self.active_streams.items():
            process = stream_info['process']
            uptime = datetime.now(tz.UTC) - stream_info['started_at']
            idle = datetime.now(tz.UTC) - stream_info['last_accessed']
            
            status[channel_id] = {
                'event_name': stream_info['event_name'],
                'started_at': stream_info['started_at'].isoformat(),
                'uptime_seconds': int(uptime.total_seconds()),
                'idle_seconds': int(idle.total_seconds()),
                'running': process.poll() is None,
                'pid': process.pid
            }
        return status

# Global stream manager instance
stream_manager = StreamManager()