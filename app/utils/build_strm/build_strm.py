import os

def writeChannelsToFolder(channels, folder_path):
    for channel in channels:
        writeChannelToFolder(channel, folder_path)

    return True

def writeChannelToFolder(channel, folder_path):
    item_file_path = os.path.join(folder_path, f"{channel['event_id']}.strm")
    with open(item_file_path, 'w') as f:
        f.write(channel["stream_url"])

    item_metadata_path = os.path.join(folder_path, f"{channel['event_id']}.nfo")
    with open(item_metadata_path, 'w') as f:
        f.write(f"<movie>\n  <title>{channel['event_name']}</title>\n  <plot>Starting at {channel['start_time']} on {channel['date']}\n  Action from {channel['league']} as {channel['home_team']} plays against {channel['away_team']}</plot>\n  <premiered>{channel['date']}</premiered>\n  <uniqueid>{channel['event_id']}</uniqueid>\n</movie>\n")
