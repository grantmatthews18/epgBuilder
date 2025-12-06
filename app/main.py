import time
import os
from build_functions import *

M3U_URL = os.getenv("M3U_URL", "https://m3u-editor-url/playlist.m3u")
OUTPUT_XMLTV = "/output/epg.xml"

def job():
    print("Fetching M3U file from:", M3U_URL)
    m3u_data = fetch_m3u(M3U_URL)

    print("Parsing channels…")
    channels = parse_channels(m3u_data)

    print("Generating XMLTV…")
    generate_xmltv(channels, OUTPUT_XMLTV)

    print("XMLTV updated.")

print("Starting XMLTV generator…")
job()  # run immediately on startup

