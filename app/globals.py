import os

# Database for storing Sports Events from ESPN
ESPN_EVENTS_DB_PATH = '/app/database/espn_database.sqlite'
ESPN_DB_MANAGER = None

# Global m3u reader object
M3U_READER = None

CONFIG = {}

M3U_URLS = []

# STRM_OUTPUT_FOLDER
STRM_OUTPUT_FOLDER = os.getenv('STRM_OUTPUT_FOLDER', '/output')


