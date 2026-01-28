# FROM python:3.12-slim

# RUN apt-get update && \
#     apt-get install -y cron ffmpeg && \
#     rm -rf /var/lib/apt/lists/*

# COPY entrypoint.sh /entrypoint.sh
# RUN chmod +x /entrypoint.sh

# WORKDIR /app
# COPY app/ /app/

# RUN pip install -r requirements.txt

# WORKDIR /

# VOLUME ["/output"]
# VOLUME ["/config"]

# ENTRYPOINT ["/entrypoint.sh"]

FROM python:3.12-slim

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY app/ /app/

WORKDIR /

# Install Python dependencies for the EPG builder
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt


VOLUME ["/output"]
# VOLUME ["/config"]

ENTRYPOINT ["/entrypoint.sh"]
