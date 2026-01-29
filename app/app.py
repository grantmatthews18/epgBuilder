import yaml
from datetime import datetime, timedelta
import os

import app.utils.database_functions.database_functions as database_functions
import app.utils.m3u_functions.m3u_functions as m3u_functions
import app.utils.build_strm.build_strm as build_strm
import app.utils.event_processor.event_processor as event_processor
import api as api

import app.globals as globals

# Update function
def update():
    # Update ESPN database with latest events from ESPN
    current_date = datetime.now().strftime("%Y-%m-%d")
    for offset in range(-3, 3):
        date_to_update = (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")
        globals.ESPN_DB_MANAGER.update_database(date_to_update)
    print("ESPN database updated.")

    # Clear events older than 7 days from ESPN event database
    cutoff_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        globals.ESPN_DB_MANAGER.clear_database_before_date(cutoff_date)
    except Exception as e:
        print(f"Error clearing old events: {e}")
    print("Old events cleared from ESPN database.")

    # Process Events from Each m3u URL
    for m3u in globals.CONFIG.get("m3u", []):

        # Ensure subdirectories exist for each M3U playlist
        output_folder = os.path.join(globals.STRM_OUTPUT_FOLDER.rstrip('/'), m3u.get('name', 'unknown'))
        os.makedirs(output_folder, exist_ok=True)

        channels = globals.M3U_READER.fetch_channels(m3u.get("url", None))

        if channels is None:
            print(f"Failed to fetch channels from M3U playlist: {m3u}")
            continue

        print(f"Fetched {len(channels)} channels from M3U playlist {m3u.get('name', 'unknown')}.")

        for (name, stream_url) in channels:
            event_data = event_processor.get_event_data(name, pattern=m3u.get("pattern", None), tz=m3u.get("tz", 'utc'))
            if event_data is not None:
                event_data['stream_url'] = stream_url
                output_folder = os.path.join(globals.STRM_OUTPUT_FOLDER.rstrip('/'), m3u.get('name', 'unknown'))
                build_strm.writeChannelToFolder(event_data, output_folder)

def init():

    # Load configuration from YAML file
    with open("/config/ottarr_config.yaml") as f:
        config = yaml.safe_load(f)

    globals.CONFIG = config

    # Ensure Events DB Path exists
    os.makedirs(os.path.dirname(globals.ESPN_EVENTS_DB_PATH), exist_ok=True)

    # Initialize the database manager for ESPN events
    globals.ESPN_DB_MANAGER = database_functions.ESPNDatabaseManager(globals.ESPN_EVENTS_DB_PATH)

    # Initialize the M3U reader
    globals.M3U_READER = m3u_functions.M3UReader()

    # Ensure output directories exist
    os.makedirs(globals.STRM_OUTPUT_FOLDER, exist_ok=True)

    # Run initial update to build output files
    update()

    # Start the API
    print(f"Starting API on Port {globals.CONFIG.get('port', 8080)}")
    api.run(port=globals.CONFIG.get('port', 8080))

if __name__ == "__main__":
    init()
