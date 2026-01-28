import requests

class M3UReader:
    def __init__(self):
        return

    def fetch_channels(self, m3u_url):

        if m3u_url is None:
            return None

        channels = []
        response = requests.get(m3u_url)
        response.raise_for_status()
        lines = response.text.strip().split('\n')

        i = 0
        while i < len(lines):
            if lines[i].startswith('#EXTINF'):
                # Extract channel name from EXTINF line
                channel_name = lines[i].split(',', 1)[-1].strip()
                # Next line should be the stream URL
                if i + 1 < len(lines):
                    stream_url = lines[i + 1].strip()
                    channels.append((channel_name, stream_url))
                    i += 2
                else:
                    i += 1
            else:
                i += 1
            
        return channels

