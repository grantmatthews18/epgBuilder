import time
import os
from build_functions import *

M3U_URL = os.getenv("M3U_URL", "https://m3u-editor-url/playlist.m3u")
OUTPUT_XMLTV = "/output/epg.xml"
OUTPUT_COMBINED_XMLTV = "/output/epg_combined.xml"
OUTPUT_M3U = "/output/playlist.m3u"

def job():
    print("Fetching M3U file from:", M3U_URL)
    m3u_data = fetch_m3u(M3U_URL)

    print("Parsing channels…")
    channels = parse_channels(m3u_data)

    print("Generating XMLTV…")
    generate_xmltv(channels, OUTPUT_XMLTV)

    print("Generating M3U…")
    generate_m3u(channels, OUTPUT_M3U)

    print("Creating combined channels…")
    combined_channels = create_combined_channels(channels)
    save_schedule(combined_channels)

    print("Generating combined XMLTV…")
    generate_combined_xmltv(combined_channels, OUTPUT_COMBINED_XMLTV)

    print("XMLTV updated.")

print("Starting XMLTV generator…")
job()  # run immediately on startup

