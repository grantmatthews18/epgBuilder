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
        self.concat_dir = "/output/concat"
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.concat_dir, exist_ok=True)
        
        # Cleanup old files on startup
        self.cleanup_old_streams()
    
    def cleanup_old_streams(self):
        """Remove old HLS and concat directories"""
        try:
            for item in os.listdir(self.output_dir):
                item_path = os.path.join(self.output_dir, item)
                if os.path.isdir(item_path):
                    import shutil
                    shutil.rmtree(item_path)
                    print(f"[CLEANUP] Removed old HLS directory: {item}")
            
            for item in os.listdir(self.concat_dir):
                item_path = os.path.join(self.concat_dir, item)
                if os.path.isfile(item_path):
                    os.remove(item_path)
                    print(f"[CLEANUP] Removed old concat file: {item}")
        except Exception as e:
            print(f"[CLEANUP] Error: {e}")
    
    def load_schedule(self):
        try:
            with open("/output/schedule.json", "r") as f:
                return json.load(f)
        except:
            return {}
    
    def get_service_channel(self, channel_id):
        """Find service channel by ID"""
        schedule = self.load_schedule()
        
        for pattern_name, pattern_data in schedule.items():
            for ch in pattern_data["service_channels"]:
                if ch["id"] == channel_id:
                    return ch
        return None
    
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
    
    def create_concat_playlist(self, channel_id, service_channel):
        """Create FFmpeg concat playlist with all events for this channel"""
        concat_file = f"{self.concat_dir}/{channel_id}.txt"
        
        # Get all programs with stream URLs
        programs_with_streams = [p for p in service_channel["programs"] if p.get("stream_url")]
        
        if not programs_with_streams:
            return None
        
        # Create concat playlist
        # Each line: file 'stream_url'
        with open(concat_file, 'w') as f:
            for program in programs_with_streams:
                # FFmpeg concat needs file protocol
                f.write(f"file '{program['stream_url']}'\n")
        
        return concat_file
    
    def update_current_stream_marker(self, channel_id, current_event_index):
        """Update a marker file to indicate which stream should be playing"""
        marker_file = f"{self.concat_dir}/{channel_id}.marker"
        
        with open(marker_file, 'w') as f:
            f.write(str(current_event_index))
    
    def get_current_stream_url(self, channel_id, service_channel):
        """Get the stream URL that should be playing now"""
        current_event = self.get_current_event(service_channel)
        
        if current_event and current_event.get("stream_url"):
            return current_event["stream_url"]
        
        return None
    
    def start_stream(self, channel_id):
        """Start FFmpeg process for a channel with dynamic stream switching"""
        lock = self.get_lock(channel_id)
        
        with lock:
            # Check if already streaming
            if channel_id in self.active_streams:
                active = self.active_streams[channel_id]
                if active['process'].poll() is None:
                    # Already streaming and process is alive
                    active['last_accessed'] = datetime.now(tz.UTC)
                    print(f"[STREAM] Already streaming {channel_id}")
                    return active['playlist_path']
                else:
                    # Dead process, clean up
                    print(f"[STREAM] Cleaning up dead stream for {channel_id}")
                    self._stop_stream_locked(channel_id)
            
            service_channel = self.get_service_channel(channel_id)
            if not service_channel:
                print(f"[STREAM] Channel not found: {channel_id}")
                return None
            
            # Get current stream URL
            current_stream_url = self.get_current_stream_url(channel_id, service_channel)
            
            if not current_stream_url:
                print(f"[STREAM] No current event for {channel_id}")
                return None
            
            output_path = f"{self.output_dir}/{channel_id}"
            os.makedirs(output_path, exist_ok=True)
            
            playlist_path = f"{output_path}/stream.m3u8"
            input_file = f"{self.concat_dir}/{channel_id}_current.txt"
            
            # Create initial input file with current stream
            with open(input_file, 'w') as f:
                f.write(f"file '{current_stream_url}'\n")
            
            # FFmpeg command using concat demuxer with auto-update
            ffmpeg_cmd = [
                'ffmpeg',
                '-re',  # Read input at native frame rate
                '-f', 'concat',
                '-safe', '0',
                '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                '-i', input_file,
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
            print(f"[STREAM] Initial stream: {current_stream_url}")
            
            try:
                process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    preexec_fn=os.setsid
                )
                
                self.active_streams[channel_id] = {
                    'process': process,
                    'current_stream_url': current_stream_url,
                    'input_file': input_file,
                    'started_at': datetime.now(tz.UTC),
                    'last_accessed': datetime.now(tz.UTC),
                    'playlist_path': playlist_path,
                    'service_channel': service_channel
                }
                
                # Start monitoring thread for this stream
                monitor_thread = threading.Thread(
                    target=self._monitor_and_switch_stream,
                    args=(channel_id,),
                    daemon=True
                )
                monitor_thread.start()
                
                print(f"[STREAM] FFmpeg started (PID: {process.pid})")
                return playlist_path
                
            except Exception as e:
                print(f"[STREAM] Error starting FFmpeg: {e}")
                return None
    
    def _monitor_and_switch_stream(self, channel_id):
        """Monitor stream and switch inputs when events change"""
        while channel_id in self.active_streams:
            try:
                time.sleep(5)  # Check every 5 seconds
                
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
                    service_channel = stream_info['service_channel']
                    current_stream_url = self.get_current_stream_url(channel_id, service_channel)
                    
                    if not current_stream_url:
                        # No current event
                        print(f"[MONITOR] No current event for {channel_id}, stopping")
                        self._stop_stream_locked(channel_id)
                        break
                    
                    if current_stream_url != stream_info['current_stream_url']:
                        # Event changed! Switch stream by updating input file
                        print(f"[MONITOR] Event changed for {channel_id}")
                        print(f"[MONITOR] Old: {stream_info['current_stream_url']}")
                        print(f"[MONITOR] New: {current_stream_url}")
                        
                        # Update the concat input file
                        input_file = stream_info['input_file']
                        with open(input_file, 'w') as f:
                            f.write(f"file '{current_stream_url}'\n")
                        
                        # Restart FFmpeg with new stream
                        print(f"[MONITOR] Restarting FFmpeg with new stream")
                        self._stop_stream_locked(channel_id)
                        
                        # Release lock and restart
                        lock.release()
                        time.sleep(1)
                        self.start_stream(channel_id)
                        break
                
            except Exception as e:
                print(f"[MONITOR] Error monitoring {channel_id}: {e}")
                import traceback
                traceback.print_exc()
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
        return self.start_stream(channel_id)
    
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
            
            # Get current event info
            current_event = self.get_current_event(stream_info['service_channel'])
            
            status[channel_id] = {
                'current_stream': stream_info['current_stream_url'],
                'current_event': current_event.get('program_name') if current_event else 'No event',
                'started_at': stream_info['started_at'].isoformat(),
                'uptime_seconds': int(uptime.total_seconds()),
                'idle_seconds': int(idle.total_seconds()),
                'running': process.poll() is None,
                'pid': process.pid
            }
        return status

# Global stream manager instance
stream_manager = StreamManager()
