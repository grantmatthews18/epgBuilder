import os
import threading
import time
import json
import requests
from datetime import datetime, timedelta
from dateutil import tz, parser as dtparse

class StreamManager:
    def __init__(self):
        self.active_streams = {}  # channel_id -> stream info
        self.stream_locks = {}  # channel_id -> threading.Lock
        self.idle_timeout = 300  # 5 minutes
    
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
            for service_channel in pattern_data.get("service_channels", []):
                if service_channel["id"] == channel_id:
                    return service_channel
        return None
    
    def get_current_event(self, service_channel):
        """Find which event is currently live"""
        now = datetime.now(tz.UTC)
        
        for program in service_channel["programs"]:
            start_str = program.get("start")
            stop_str = program.get("stop")
            
            if not start_str or not stop_str:
                continue
            
            start = dtparse.parse(start_str)
            stop = dtparse.parse(stop_str)
            
            if start <= now < stop:
                return program
        
        return None
    
    def get_lock(self, channel_id):
        """Get or create lock for a channel"""
        if channel_id not in self.stream_locks:
            self.stream_locks[channel_id] = threading.Lock()
        return self.stream_locks[channel_id]
    
    def get_current_stream_url(self, channel_id):
        """Get the stream URL that should be playing now"""
        service_channel = self.get_service_channel(channel_id)
        
        if not service_channel:
            print(f"[PROXY] Channel {channel_id} not found")
            return None
        
        current_event = self.get_current_event(service_channel)
        
        if current_event and current_event.get("stream_url"):
            return current_event["stream_url"]
        
        print(f"[PROXY] No active event for {channel_id}")
        return None
    
    def start_proxy(self, channel_id):
        """Start monitoring and proxying for a channel"""
        lock = self.get_lock(channel_id)
        
        with lock:
            if channel_id in self.active_streams:
                # Already active, just update access time
                self.active_streams[channel_id]["last_access"] = time.time()
                return True
            
            stream_url = self.get_current_stream_url(channel_id)
            
            if not stream_url:
                print(f"[PROXY] No stream URL available for {channel_id}")
                return False
            
            print(f"[PROXY] Starting proxy for {channel_id} -> {stream_url}")
            
            # Store stream info
            self.active_streams[channel_id] = {
                "stream_url": stream_url,
                "start_time": time.time(),
                "last_access": time.time(),
                "last_check": time.time()
            }
            
            # Start monitoring thread
            monitor_thread = threading.Thread(
                target=self._monitor_stream,
                args=(channel_id,),
                daemon=True
            )
            monitor_thread.start()
            
            return True
    
    def _monitor_stream(self, channel_id):
        """Monitor stream and update URL when events change"""
        check_interval = 5  # Check every 5 seconds
        
        while channel_id in self.active_streams:
            try:
                time.sleep(check_interval)
                
                lock = self.get_lock(channel_id)
                with lock:
                    if channel_id not in self.active_streams:
                        break
                    
                    stream_info = self.active_streams[channel_id]
                    
                    # Check for idle timeout
                    idle_time = time.time() - stream_info["last_access"]
                    if idle_time > self.idle_timeout:
                        print(f"[PROXY] {channel_id} idle for {idle_time:.0f}s, stopping")
                        del self.active_streams[channel_id]
                        break
                    
                    # Check if we need to switch streams
                    current_url = self.get_current_stream_url(channel_id)
                    
                    if not current_url:
                        print(f"[PROXY] No current stream URL for {channel_id}, stopping")
                        del self.active_streams[channel_id]
                        break
                    
                    if current_url != stream_info["stream_url"]:
                        print(f"[PROXY] {channel_id} switching from {stream_info['stream_url']} to {current_url}")
                        stream_info["stream_url"] = current_url
                        stream_info["last_check"] = time.time()
                    
            except Exception as e:
                print(f"[PROXY] Monitor error for {channel_id}: {e}")
                break
        
        print(f"[PROXY] Monitor stopped for {channel_id}")
    
    def proxy_stream(self, channel_id):
        """
        Generator that yields chunks of the current stream.
        Automatically switches sources when events change.
        """
        # Ensure proxy is started
        if not self.start_proxy(channel_id):
            return
        
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        current_url = None
        current_response = None
        
        try:
            while True:
                # Update access time
                self.update_access_time(channel_id)
                
                # Check if we're still active
                if channel_id not in self.active_streams:
                    print(f"[PROXY] {channel_id} no longer active, stopping proxy")
                    break
                
                # Get current stream URL
                stream_info = self.active_streams.get(channel_id)
                if not stream_info:
                    break
                
                new_url = stream_info["stream_url"]
                
                # If URL changed, close old connection and open new one
                if new_url != current_url:
                    if current_response:
                        current_response.close()
                    
                    print(f"[PROXY] {channel_id} opening stream: {new_url}")
                    current_url = new_url
                    
                    try:
                        current_response = session.get(new_url, stream=True, timeout=10)
                        current_response.raise_for_status()
                    except Exception as e:
                        print(f"[PROXY] Error opening {new_url}: {e}")
                        time.sleep(1)
                        continue
                
                # Stream chunks from current source
                try:
                    for chunk in current_response.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                        
                        # Check if URL changed (every few chunks)
                        if channel_id in self.active_streams:
                            stream_info = self.active_streams[channel_id]
                            if stream_info["stream_url"] != current_url:
                                print(f"[PROXY] {channel_id} detected URL change mid-stream")
                                break
                        else:
                            print(f"[PROXY] {channel_id} deactivated mid-stream")
                            return
                    
                    # If we got here, stream ended naturally
                    print(f"[PROXY] {channel_id} stream ended, checking for next event")
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"[PROXY] Error streaming {current_url}: {e}")
                    time.sleep(1)
                    
        finally:
            if current_response:
                current_response.close()
            session.close()
            print(f"[PROXY] Closed proxy for {channel_id}")
    
    def update_access_time(self, channel_id):
        """Update last access time to prevent idle timeout"""
        if channel_id in self.active_streams:
            self.active_streams[channel_id]["last_access"] = time.time()
    
    def stop_stream(self, channel_id):
        """Stop proxying a stream"""
        lock = self.get_lock(channel_id)
        with lock:
            if channel_id in self.active_streams:
                print(f"[PROXY] Stopping {channel_id}")
                del self.active_streams[channel_id]
                return True
        return False
    
    def get_stream_status(self):
        """Get status of all active streams"""
        status = {}
        for channel_id, info in self.active_streams.items():
            uptime = time.time() - info["start_time"]
            idle = time.time() - info["last_access"]
            status[channel_id] = {
                "stream_url": info["stream_url"],
                "uptime_seconds": int(uptime),
                "idle_seconds": int(idle),
                "last_check": info["last_check"]
            }
        return status

# Global stream manager instance
stream_manager = StreamManager()
