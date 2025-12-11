const http = require('http');
const https = require('https');
const fs = require('fs').promises;
const url = require('url');

const SERVER_PORT = process.env.SERVER_PORT || 8080;
const SCHEDULE_PATH = '/output/schedule.json';
const DEFAULT_EVENT_IMG = process.env.DEFAULT_EVENT_IMG || '';

// Disable SSL certificate validation (like Python's verify=False)
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';

let scheduleCache = null;
let lastScheduleLoad = 0;
const SCHEDULE_CACHE_TTL = 5000; // 5 seconds

async function loadSchedule() {
    const now = Date.now();
    if (scheduleCache && (now - lastScheduleLoad) < SCHEDULE_CACHE_TTL) {
        return scheduleCache;
    }
    
    try {
        const data = await fs.readFile(SCHEDULE_PATH, 'utf8');
        scheduleCache = JSON.parse(data);
        lastScheduleLoad = now;
        console.log('[SCHEDULE] Loaded successfully');
        return scheduleCache;
    } catch (error) {
        console.error('[SCHEDULE] Error loading schedule:', error.message);
        return scheduleCache || {};
    }
}


async function streamTS(sourceUrl, res, req) {
    const TS_PACKET_SIZE = 188;

    let buffer = Buffer.alloc(0);
    let bytesSent = 0;

    // Minimal PAT/PMT to send immediately
    const initialPackets = Buffer.alloc(TS_PACKET_SIZE * 2);
    // PAT (PID 0x0000)
    initialPackets[0] = 0x47;
    initialPackets[1] = 0x40;
    initialPackets[2] = 0x00;
    initialPackets[3] = 0x10;
    // PMT (PID 0x1000)
    initialPackets[188] = 0x47;
    initialPackets[189] = 0x50;
    initialPackets[190] = 0x00;
    initialPackets[191] = 0x10;

    // Send headers immediately
    res.writeHead(200, {
        'Content-Type': 'video/mp2t',
        'Transfer-Encoding': 'chunked',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Access-Control-Allow-Origin': '*',
        'Accept-Ranges': 'bytes',
        'Server': 'PlexTSProxy'
    });

    // Send initial PAT/PMT packets immediately
    res.write(initialPackets);
    bytesSent += initialPackets.length;
    console.log('[STREAM] Sent initial PAT/PMT packets');

    const fetchStream = (urlToFetch) => {
        const urlObj = new URL(urlToFetch);
        const protocol = urlObj.protocol === 'https:' ? require('https') : require('http');

        const request = protocol.get(
            {
                hostname: urlObj.hostname,
                port: urlObj.port || (urlObj.protocol === 'https:' ? 443 : 80),
                path: urlObj.pathname + urlObj.search,
                headers: {
                    'User-Agent': req.headers['user-agent'] || 'Mozilla/5.0',
                    'Connection': 'keep-alive',
                    'Accept': '*/*',
                    'Range': req.headers.range || 'bytes=0-'
                }
            },
            upstream => {
                // Follow redirects
                if (upstream.statusCode >= 300 && upstream.statusCode < 400 && upstream.headers.location) {
                    const redirectUrl = new URL(upstream.headers.location, urlToFetch).toString();
                    upstream.destroy();
                    console.log(`[STREAM] Redirect → ${redirectUrl}`);
                    return fetchStream(redirectUrl);
                }

                if (upstream.statusCode !== 200 && upstream.statusCode !== 206) {
                    if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'text/plain' });
                    return res.end('Bad Gateway');
                }

                upstream.on('data', chunk => {
                    buffer = Buffer.concat([buffer, chunk]);

                    while (buffer.length >= TS_PACKET_SIZE) {
                        const syncIdx = buffer.indexOf(0x47);
                        if (syncIdx === -1) {
                            buffer = Buffer.alloc(0);
                            break;
                        }

                        if (syncIdx > 0) buffer = buffer.slice(syncIdx);
                        if (buffer.length < TS_PACKET_SIZE) break;

                        const packet = buffer.slice(0, TS_PACKET_SIZE);
                        buffer = buffer.slice(TS_PACKET_SIZE);

                        res.write(packet);
                        bytesSent += TS_PACKET_SIZE;
                    }
                });

                upstream.on('end', () => {
                    console.log(`[STREAM] Upstream ended, sent ${(bytesSent / 1024 / 1024).toFixed(2)} MB`);
                    res.end();
                });

                upstream.on('error', err => {
                    console.error('[STREAM] Upstream error:', err.message);
                    res.end();
                });

                res.on('close', () => {
                    upstream.destroy();
                });
            }
        );

        request.on('error', err => {
            console.error('[STREAM] Request error:', err.message);
            if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'text/plain' });
            res.end('Connection error');
        });
    };

    fetchStream(sourceUrl);
}


function findChannelAndEvent(schedule, channelId) {
    let channel = null;
    
    // Decode the channel ID in case it was URL encoded
    const decodedChannelId = decodeURIComponent(channelId);
    
    for (const [patternName, patternData] of Object.entries(schedule)) {
        const serviceChannels = patternData.service_channels || [];
        for (const serviceChannel of serviceChannels) {
            // Match by channel name (human-friendly) or old id for backward compatibility
            if (serviceChannel.channel_name === decodedChannelId || serviceChannel.id === channelId) {
                channel = serviceChannel;
                break;
            }
        }
        if (channel) break;
    }
    
    if (!channel) {
        return { channel: null, event: null };
    }
    
    const now = new Date();
    let event = null;
    
    // Find the current event without filling gaps
    for (const program of channel.programs || []) {
        if (!program.start_dt || !program.stop_dt) continue;
        
        const startDt = new Date(program.start_dt);
        const stopDt = new Date(program.stop_dt);
        
        if (startDt <= now && now < stopDt) {
            event = program;
            break;
        }
    }
    
    return { channel, event };
}

function escapeXml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
}

function formatXmltvTimestamp(date) {
    const year = date.getUTCFullYear();
    const month = String(date.getUTCMonth() + 1).padStart(2, '0');
    const day = String(date.getUTCDate()).padStart(2, '0');
    const hours = String(date.getUTCHours()).padStart(2, '0');
    const minutes = String(date.getUTCMinutes()).padStart(2, '0');
    const seconds = String(date.getUTCSeconds()).padStart(2, '0');
    return `${year}${month}${day}${hours}${minutes}${seconds} +0000`;
}



const server = http.createServer(async (req, res) => {
    const parsedUrl = url.parse(req.url, true);
    const pathname = parsedUrl.pathname;
    
    console.log(`[REQUEST] ${req.method} ${pathname} from ${req.socket.remoteAddress}`);
    
    // Health endpoint
    if (pathname === '/health') {
        const schedule = await loadSchedule();
        const totalChannels = Object.values(schedule).reduce((sum, p) => 
            sum + (p.service_channels || []).length, 0
        );
        
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            status: 'ok',
            total_channels: totalChannels,
            timestamp: new Date().toISOString()
        }));
        return;
    }
    
    // Root endpoint
    if (pathname === '/') {
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(`
<!DOCTYPE html>
<html>
<head><title>EPG Builder</title></head>
<body style="font-family: Arial; margin: 40px;">
    <h1>EPG Builder - Combined Channels</h1>
    <div><a href="/playlist.m3u">M3U Playlist</a></div>
    <div><a href="/epg.xml">XMLTV EPG</a></div>
    <div><a href="/health">Health</a></div>
</body>
</html>
        `);
        return;
    }
    
    // Playlist endpoint
    if (pathname === '/playlist.m3u') {
        const schedule = await loadSchedule();
        const baseUrl = `http://${req.headers.host}`;
        const lines = ['#EXTM3U'];
        
        for (const [patternName, patternData] of Object.entries(schedule)) {
            for (const channel of patternData.service_channels || []) {
                if (channel.programs && channel.programs.length > 0) {
                    const channelId = channel.channel_name; // Use human-friendly name as ID
                    const extinf = `#EXTINF:-1 tvg-id="${channelId}" tvg-name="${channel.channel_name}" tvg-logo="${channel.icon_url}" group-title="${patternData.category}",${channel.channel_name}`;
                    const streamUrl = `${baseUrl}/stream/${encodeURIComponent(channelId)}.ts`;
                    lines.push(extinf, streamUrl);
                }
            }
        }
        
        res.writeHead(200, { 
            'Content-Type': 'audio/x-mpegurl',
            'Cache-Control': 'no-cache'
        });
        res.end(lines.join('\n'));
        return;
    }
    
    // EPG XML endpoint
    if (pathname === '/epg.xml') {
        const schedule = await loadSchedule();
        const lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<tv generator-info-name="epgBuilder-combined">'
        ];
        
        // Calculate hidden event time: 00:00 UTC from 1 day ago
        const now = new Date();
        const hiddenEventStart = new Date(now);
        hiddenEventStart.setUTCDate(hiddenEventStart.getUTCDate() - 1);
        hiddenEventStart.setUTCHours(0, 0, 0, 0);
        
        const hiddenEventStop = new Date(hiddenEventStart);
        hiddenEventStop.setUTCMinutes(30); // 30 minutes duration
        
        // Channels (only include channels with programs)
        for (const [patternName, patternData] of Object.entries(schedule)) {
            for (const channel of patternData.service_channels || []) {
                if (channel.programs && channel.programs.length > 0) {
                    const channelId = channel.channel_name; // Use human-friendly name as ID
                    lines.push(`  <channel id="${escapeXml(channelId)}">`);
                    lines.push(`    <display-name>${escapeXml(channel.channel_name)}</display-name>`);
                    if (channel.icon_url) {
                        lines.push(`    <icon src="${escapeXml(channel.icon_url)}"/>`);
                    }
                    lines.push('  </channel>');
                }
            }
        }
        
        // Programs (no placeholders, just real events)
        for (const [patternName, patternData] of Object.entries(schedule)) {
            for (const channel of patternData.service_channels || []) {
                // Only process channels that have programs
                if (!channel.programs || channel.programs.length === 0) {
                    continue;
                }
                
                const channelId = channel.channel_name; // Use human-friendly name as ID
                
                // Add the hidden event for IPTV player detection (00:00 UTC -1 day, 30 min duration)
                lines.push(`  <programme channel="${escapeXml(channelId)}" start="${formatXmltvTimestamp(hiddenEventStart)}" stop="${formatXmltvTimestamp(hiddenEventStop)}">`);
                lines.push(`    <title>${escapeXml(channel.channel_name)} - Hidden Event</title>`);
                lines.push(`    <desc>Hidden event for IPTV player channel detection</desc>`);
                if (patternData.category) {
                    lines.push(`    <category lang="en">${escapeXml(patternData.category)}</category>`);
                }
                if (channel.icon_url) {
                    lines.push(`    <icon src="${escapeXml(channel.icon_url)}"/>`);
                }
                lines.push('  </programme>');
                
                // Add all programs (placeholders already removed at source)
                for (const program of channel.programs) {
                    lines.push(`  <programme channel="${escapeXml(channelId)}" start="${program.start_str}" stop="${program.stop_str}">`);
                    lines.push(`    <title>${escapeXml(program.program_name)}</title>`);
                    lines.push(`    <desc>${escapeXml(program.description)}</desc>`);
                    if (patternData.category) {
                        lines.push(`    <category lang="en">${escapeXml(patternData.category)}</category>`);
                    }
                    if (program.icon_url) {
                        lines.push(`    <icon src="${escapeXml(program.icon_url)}"/>`);
                    }
                    lines.push('  </programme>');
                }
            }
        }
        
        lines.push('</tv>');
        
        res.writeHead(200, { 
            'Content-Type': 'application/xml',
            'Cache-Control': 'no-cache, no-store, must-revalidate'
        });
        res.end(lines.join('\n'));
        return;
    }
    
    // Stream endpoint (handles both GET and HEAD)
    const streamMatch = pathname.match(/^\/stream\/([^\/]+?)(\.ts)?$/);
    if (streamMatch) {
        const channelId = decodeURIComponent(streamMatch[1]);
        console.log(`[STREAM] Looking up channel: ${channelId}`);
        
        const schedule = await loadSchedule();
        const { channel, event } = findChannelAndEvent(schedule, channelId);
        
        if (!channel) {
            console.error(`[STREAM] Channel ${channelId} not found`);
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            res.end('Channel not found');
            return;
        }
        
        if (!event) {
            console.error(`[STREAM] No active event for ${channelId}`);
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            res.end('No active event');
            return;
        }
        
        // If no stream URL in current event, find the most recent event with a stream URL
        let streamEvent = event;
        if (!event.stream_url) {
            console.log(`[STREAM] No stream URL in current event, looking for fallback stream`);
            
            // Find the most recent program with a stream URL
            let mostRecentEvent = null;
            
            for (const program of channel.programs || []) {
                if (program.stream_url) {
                    const programStop = new Date(program.stop_dt);
                    if (!mostRecentEvent) {
                        mostRecentEvent = program;
                    } else {
                        const mostRecentStop = new Date(mostRecentEvent.stop_dt);
                        if (programStop > mostRecentStop) {
                            mostRecentEvent = program;
                        }
                    }
                }
            }
            
            if (mostRecentEvent) {
                streamEvent = mostRecentEvent;
                console.log(`[STREAM] Using fallback event: ${streamEvent.program_name}`);
            } else {
                console.error(`[STREAM] No stream URL available for ${channelId}`);
                res.writeHead(503, { 'Content-Type': 'text/plain' });
                res.end('No stream available');
                return;
            }
        }
        
        console.log(`[STREAM] Streaming event: ${streamEvent.program_name}`);
        
        // Handle HEAD request - return headers only, no body
        if (req.method === 'HEAD') {
            res.writeHead(200, {
                'Date': new Date().toUTCString(),
                'Content-Type': 'video/mp2t',
                'Content-Length': '0',
                'Connection': 'keep-alive',
                'Pragma': 'public',
                'Cache-Control': 'public, must-revalidate, proxy-revalidate'
            });
            res.end();
            return;
        }
        
        // Handle GET request - stream the content
        streamTS(streamEvent.stream_url, res, req);
        return;
    }
    
    // 404 for everything else
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not Found');
});

server.keepAliveTimeout = 120000; // 2 minutes
server.headersTimeout = 125000; // Slightly more than keepAliveTimeout

server.listen(SERVER_PORT, '0.0.0.0', () => {
    console.log(`[SERVER] EPG Builder listening on port ${SERVER_PORT}`);
    console.log(`[SERVER] Schedule path: ${SCHEDULE_PATH}`);
});

server.on('error', (error) => {
    console.error('[SERVER] Server error:', error);
});