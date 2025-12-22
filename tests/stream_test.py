import requests

TS_PACKET_SIZE = 188
SYNC_BYTE = 0x47

def is_ts_stream_active(url, timeout=10):
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()

            data = b''
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    data += chunk
                if len(data) >= 4096:
                    break

            if len(data) < TS_PACKET_SIZE:
                return False, "Not enough data received"

            # Check for MPEG-TS sync bytes
            sync_count = 0
            for i in range(0, len(data) - TS_PACKET_SIZE, TS_PACKET_SIZE):
                if data[i] == SYNC_BYTE:
                    sync_count += 1

            if sync_count >= 3:
                return True, "Active MPEG-TS stream detected"
            else:
                return False, "Data received, but not valid MPEG-TS"

    except Exception as e:
        return False, str(e)

if __name__ == "__main__":
    url = " http://192.168.1.254:36400/shared/stream/NzYyOA.ts"
    active, reason = is_ts_stream_active(url)
    print(f"Active: {active} | {reason}")
