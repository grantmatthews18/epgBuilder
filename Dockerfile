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

FROM node:18-slim

RUN apt-get update && \
    apt-get install -y cron python3 python3-pip python3-venv && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment for Python
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app
COPY app/ /app/

# Install Python dependencies for the EPG builder
RUN pip install --no-cache-dir -r requirements.txt

# Install Node.js dependencies
RUN npm install

WORKDIR /

VOLUME ["/output"]
VOLUME ["/config"]

ENTRYPOINT ["/entrypoint.sh"]
