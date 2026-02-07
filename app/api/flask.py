from flask import Flask, jsonify, request, Response, stream_with_context, abort

import os
import threading
import subprocess
import uuid
import time
import shutil
import logging
from urllib.parse import unquote_plus, urlparse

app = Flask(__name__)
logger = logging.getLogger(__name__)


@app.route('/api/hello', methods=['GET'])
def hello():
    return jsonify({'message': 'Hello, world!'})


# class StreamManager:
#     """Manages a single ffmpeg process that transcodes an input stream into
#     a fragmented MP4 file which is then streamed to a single active client.

#     Behavior:
#     - start_stream(stream_url, client_id) makes the provided client the active
#       client and ensures an ffmpeg process is running that reads from stream_url
#       and writes to a known output file.
#     - If a new client starts a stream, it becomes the active client and the
#       previous ffmpeg process (if any) is terminated and restarted for the new
#       stream URL.
#     - When the active client disconnects, ffmpeg is terminated.
#     """

#     def __init__(self, output_dir=None):
#         self.lock = threading.Lock()
#         self.ffmpeg_process = None
#         self.current_stream_url = None
#         self.active_client_id = None
#         self.output_dir = output_dir or os.path.join(os.getcwd(), 'output')
#         # single output file used for the currently active stream
#         self.output_file = os.path.join(self.output_dir, 'active_stream.mp4')
#         self.ffmpeg_bin = shutil.which('ffmpeg')
#         self._monitor_thread = None

#     def _validate_url(self, url: str) -> bool:
#         # Basic validation: check for a supported scheme or a local file path
#         parsed = urlparse(url)
#         if parsed.scheme:
#             return parsed.scheme in ('http', 'https', 'rtmp', 'rtsp', 'udp', 'mms')
#         # allow local files or other inputs as well
#         return True

#     def _start_ffmpeg(self, stream_url: str, output_path: str) -> subprocess.Popen:
#         if not self.ffmpeg_bin:
#             raise RuntimeError('ffmpeg not found in PATH; please install ffmpeg')

#         # Remove any previous file and create an empty placeholder so callers
#         # can open it immediately.
#         try:
#             if os.path.exists(output_path):
#                 os.remove(output_path)
#         except Exception:
#             logger.exception('Could not remove existing output file')

#         open(output_path, 'wb').close()

#         cmd = [
#             self.ffmpeg_bin,
#             '-hide_banner', '-loglevel', 'warning',
#             '-y',
#             '-i', stream_url,
#             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
#             '-c:a', 'aac', '-b:a', '128k',
#             '-f', 'mp4',
#             '-movflags', '+frag_keyframe+empty_moov+default_base_moof+faststart',
#             output_path,
#         ]

#         logger.info('Starting ffmpeg: %s', ' '.join(cmd))
#         # Redirect stdio to devnull to avoid blocking
#         proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#         return proc

#     def start_stream(self, stream_url: str, client_id: str) -> str:
#         with self.lock:
#             if not self._validate_url(stream_url):
#                 raise ValueError('Invalid or unsupported stream_url')

#             # Mark this client as the active one. If the stream URL is the same
#             # and ffmpeg is already running, keep it; otherwise restart ffmpeg.
#             self.active_client_id = client_id

#             if self.current_stream_url == stream_url and self.ffmpeg_process and self.process_running():
#                 logger.info('Reusing existing ffmpeg for the same stream_url')
#                 return self.output_file

#             # switch streams: terminate previous process if one exists
#             if self.ffmpeg_process:
#                 self._terminate_process_locked()

#             self.current_stream_url = stream_url
#             os.makedirs(self.output_dir, exist_ok=True)
#             self.ffmpeg_process = self._start_ffmpeg(stream_url, self.output_file)
#             # start a background monitor that clears process state when it exits
#             self._start_monitor_thread()
#             return self.output_file

#     def _start_monitor_thread(self):
#         if self._monitor_thread and self._monitor_thread.is_alive():
#             return

#         def monitor():
#             proc = None
#             with self.lock:
#                 proc = self.ffmpeg_process
#             if not proc:
#                 return
#             proc.wait()
#             logger.info('ffmpeg exited')
#             with self.lock:
#                 # cleanup state if this is the process we started
#                 if self.ffmpeg_process is proc:
#                     self.ffmpeg_process = None
#                     self.current_stream_url = None
#                     # keep output file around for a short time; callers will
#                     # observe lack of running process and finish

#         self._monitor_thread = threading.Thread(target=monitor, daemon=True)
#         self._monitor_thread.start()

#     def _terminate_process_locked(self):
#         if self.ffmpeg_process:
#             proc = self.ffmpeg_process
#             if proc.poll() is None:
#                 logger.info('Terminating ffmpeg process')
#                 proc.terminate()
#                 try:
#                     proc.wait(timeout=5)
#                 except subprocess.TimeoutExpired:
#                     proc.kill()
#                     proc.wait()
#             self.ffmpeg_process = None

#     def stop_stream(self):
#         with self.lock:
#             self._terminate_process_locked()
#             self.current_stream_url = None
#             self.active_client_id = None

#     def client_disconnected(self, client_id: str):
#         with self.lock:
#             if self.active_client_id == client_id:
#                 logger.info('Active client disconnected, stopping stream')
#                 self._terminate_process_locked()
#                 self.active_client_id = None
#                 self.current_stream_url = None

#     def process_running(self) -> bool:
#         return self.ffmpeg_process is not None and self.ffmpeg_process.poll() is None

#     def is_active_client(self, client_id: str) -> bool:
#         with self.lock:
#             return self.active_client_id == client_id


# # instantiate a module-level manager that uses the repo's output folder
# STREAM_MANAGER = StreamManager(output_dir=os.path.join(os.getcwd(), 'output'))


# @app.route('/api/stream', methods=['GET'])
# def stream_endpoint():
#     """Endpoint that accepts a single query parameter 'stream_url' (URI safe)
#     and returns a fragmented MP4 produced by an ffmpeg process which is
#     restarted whenever a new client connects with a (possibly different)
#     stream_url. The endpoint makes the requesting client the "active"
#     client; previous clients are disconnected when a new client connects.
#     """

#     stream_url = request.args.get('stream_url')
#     if not stream_url:
#         return abort(400, description='Missing stream_url parameter')

#     # Flask already decodes query strings; ensure any encoded parts are decoded
#     stream_url = unquote_plus(stream_url)

#     client_id = uuid.uuid4().hex

#     try:
#         output_path = STREAM_MANAGER.start_stream(stream_url, client_id)
#     except ValueError as e:
#         return abort(400, description=str(e))
#     except Exception as e:
#         logger.exception('Failed to start stream')
#         return abort(500, description=str(e))

#     CHUNK_SIZE = 64 * 1024

#     def generate():
#         # Wait briefly for the output file to appear (ffmpeg creates it quickly
#         # but may take a moment). If ffmpeg dies unexpectedly, give up.
#         start = time.time()
#         while not os.path.exists(output_path):
#             if not STREAM_MANAGER.process_running():
#                 return
#             if time.time() - start > 10:
#                 # file did not appear
#                 return
#             time.sleep(0.1)

#         try:
#             with open(output_path, 'rb') as f:
#                 while True:
#                     # if a new client has become active, end this response
#                     if not STREAM_MANAGER.is_active_client(client_id):
#                         return

#                     chunk = f.read(CHUNK_SIZE)
#                     if chunk:
#                         try:
#                             yield chunk
#                         except GeneratorExit:
#                             # client disconnected
#                             STREAM_MANAGER.client_disconnected(client_id)
#                             raise
#                     else:
#                         # no new data at the moment; if ffmpeg exited and no
#                         # more data will ever arrive, finish. Otherwise, wait.
#                         if not STREAM_MANAGER.process_running():
#                             break
#                         time.sleep(0.1)
#         finally:
#             # ensure that if this was the active client we stop the ffmpeg
#             STREAM_MANAGER.client_disconnected(client_id)

#     headers = {
#         'Content-Type': 'video/mp4',
#         'Cache-Control': 'no-cache',
#     }
#     return Response(stream_with_context(generate()), headers=headers)