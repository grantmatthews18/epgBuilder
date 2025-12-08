const http = require('http');
const https = require('https');
const fs = require('fs').promises;
const url = require('url');

const SERVER_PORT = process.env.SERVER_PORT || 8080;
const SCHEDULE_PATH = '/output/schedule.json';

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

function streamTS(sourceUrl, res, channelId, programName, redirectCount = 0) {
    const MAX_REDIRECTS = 5;
    
    if (redirectCount >= MAX_REDIRECTS) {
        console.error('[STREAM] Too many redirects');
        if (!res.headersSent) {
            res.writeHead(502, { 'Content-Type': 'text/plain' });
        }
        res.end('Too many redirects');
        return;
    }
    
    const TS_PACKET_SIZE = 188;
    let buffer = Buffer.alloc(0);
    let bytesSent = 0;
    let packetCount = 0;
    let syncLost = false;
    
    if (redirectCount === 0) {
        console.log(`[STREAM] ${channelId} -> ${programName}`);
    }
    console.log(`[STREAM] Source URL: ${sourceUrl}`);
    
    const parsedUrl = url.parse(sourceUrl);
    const protocol = parsedUrl.protocol === 'https:' ? https : http;
    
    console.log(`[STREAM] Protocol: ${parsedUrl.protocol}, Host: ${parsedUrl.hostname}:${parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80)}`);
    
    const reqOptions = {
        hostname: parsedUrl.hostname,
        port: parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80),
        path: parsedUrl.path,
        method: 'GET',
        headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Connection': 'keep-alive'
        },
        timeout: 30000
    };
    
    const proxyReq = protocol.request(reqOptions, (proxyRes) => {
        console.log(`[STREAM] Upstream response: ${proxyRes.statusCode}`);
        
        // Handle redirects (301, 302, 303, 307, 308)
        if (proxyRes.statusCode >= 300 && proxyRes.statusCode < 400 && proxyRes.headers.location) {
            const redirectUrl = proxyRes.headers.location;
            console.log(`[STREAM] Following redirect to: ${redirectUrl}`);
            
            // Resolve relative URLs
            const newUrl = url.resolve(sourceUrl, redirectUrl);
            
            // Consume the redirect response body to free up the connection
            proxyRes.resume();
            
            // Follow the redirect
            streamTS(newUrl, res, channelId, programName, redirectCount + 1);
            return;
        }
        
        if (proxyRes.statusCode !== 200) {
            console.error(`[STREAM] Upstream error ${proxyRes.statusCode}`);
            if (!res.headersSent) {
                res.writeHead(502, { 'Content-Type': 'text/plain' });
            }
            res.end('Bad Gateway - Upstream returned ' + proxyRes.statusCode);
            return;
        }
        
        console.log(`[STREAM] Connected successfully`);
        console.log(`[STREAM] Content-Type: ${proxyRes.headers['content-type']}`);
        
        // Send headers only once, after successful connection
        if (!res.headersSent) {
            res.writeHead(200, {
                'Date': new Date().toUTCString(),
                'Content-Type': 'video/mp2t',
                'Connection': 'keep-alive',
                'Transfer-Encoding': 'chunked',
                'Pragma': 'public',
                'Cache-Control': 'no-cache'
            });
            console.log('[STREAM] Response headers sent');
        }
        
        // Handle client disconnect
        const onClientClose = () => {
            const mb = (bytesSent / (1024 * 1024)).toFixed(2);
            console.log(`[STREAM] Client disconnected after ${mb}MB (${packetCount} packets)`);
            proxyReq.destroy();
        };
        
        const onClientError = (error) => {
            console.error('[STREAM] Response error:', error.message);
            proxyReq.destroy();
        };
        
        res.once('close', onClientClose);
        res.once('error', onClientError);
        
        proxyRes.on('data', (chunk) => {
            buffer = Buffer.concat([buffer, chunk]);
            
            // Find sync byte if we lost it
            if (!syncLost && buffer.length >= TS_PACKET_SIZE) {
                // Check if we're synced at position 0
                if (buffer[0] !== 0x47) {
                    syncLost = true;
                    console.warn('[STREAM] Lost sync, searching for sync byte...');
                }
            }
            
            // If sync is lost, find it
            if (syncLost) {
                const syncIdx = buffer.indexOf(0x47);
                if (syncIdx === -1) {
                    // No sync byte found, discard buffer and wait for more data
                    console.warn(`[STREAM] No sync byte in ${buffer.length} bytes, discarding`);
                    buffer = Buffer.alloc(0);
                    return;
                }
                if (syncIdx > 0) {
                    console.warn(`[STREAM] Found sync at offset ${syncIdx}, discarding ${syncIdx} bytes`);
                    buffer = buffer.slice(syncIdx);
                }
                syncLost = false;
            }
            
            // Process complete TS packets
            while (buffer.length >= TS_PACKET_SIZE) {
                // Double-check sync byte
                if (buffer[0] !== 0x47) {
                    syncLost = true;
                    break;
                }
                
                // Extract one TS packet
                const packet = buffer.slice(0, TS_PACKET_SIZE);
                buffer = buffer.slice(TS_PACKET_SIZE);
                
                packetCount++;
                bytesSent += packet.length;
                
                if (packetCount === 1) {
                    console.log('[STREAM] First packet sent');
                    console.log(`[STREAM] First packet hex: ${packet.slice(0, 16).toString('hex')}`);
                }
                
                if (packetCount % 1000 === 0) {
                    const mb = (bytesSent / (1024 * 1024)).toFixed(2);
                    console.log(`[STREAM] ${mb}MB sent (${packetCount} packets)`);
                }
                
                try {
                    // Write packet to response
                    const canContinue = res.write(packet);
                    if (!canContinue) {
                        // Backpressure - pause upstream
                        proxyRes.pause();
                        res.once('drain', () => {
                            proxyRes.resume();
                        });
                    }
                } catch (error) {
                    console.error('[STREAM] Write error:', error.message);
                    proxyReq.destroy();
                    return;
                }
            }
        });
        
        proxyRes.on('end', () => {
            // Clean up client listeners
            res.removeListener('close', onClientClose);
            res.removeListener('error', onClientError);
            
            const mb = (bytesSent / (1024 * 1024)).toFixed(2);
            console.log(`[STREAM] Stream ended: ${mb}MB (${packetCount} packets)`);
            
            try {
                res.end();
            } catch (error) {
                console.error('[STREAM] End error:', error.message);
            }
        });
        
        proxyRes.on('error', (error) => {
            console.error(`[STREAM] Proxy response error:`, error.message);
            res.removeListener('close', onClientClose);
            res.removeListener('error', onClientError);
            
            if (!res.headersSent) {
                res.writeHead(500, { 'Content-Type': 'text/plain' });
            }
            try {
                res.end('Stream error: ' + error.message);
            } catch (e) {
                console.error('[STREAM] Failed to send error response');
            }
        });
    });
    
    proxyReq.on('error', (error) => {
        console.error(`[STREAM] Proxy request error:`, error.message);
        if (!res.headersSent) {
            res.writeHead(502, { 'Content-Type': 'text/plain' });
        }
        try {
            res.end('Connection error: ' + error.message);
        } catch (e) {
            console.error('[STREAM] Failed to send error response');
        }
    });
    
    proxyReq.on('timeout', () => {
        console.error('[STREAM] Request timeout');
        proxyReq.destroy();
        if (!res.headersSent) {
            res.writeHead(504, { 'Content-Type': 'text/plain' });
        }
        try {
            res.end('Gateway timeout');
        } catch (e) {
            console.error('[STREAM] Failed to send timeout response');
        }
    });
    
    proxyReq.end();
}

function findChannelAndEvent(schedule, channelId) {
    let channel = null;
    
    for (const [patternName, patternData] of Object.entries(schedule)) {
        const serviceChannels = patternData.service_channels || [];
        for (const serviceChannel of serviceChannels) {
            if (serviceChannel.id === channelId) {
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
                    const extinf = `#EXTINF:-1 tvg-id="${channel.id}" tvg-name="${channel.channel_name}" tvg-logo="${channel.icon_url}" group-title="${patternData.category}",${channel.channel_name}`;
                    const streamUrl = `${baseUrl}/stream/${channel.id}.ts`;
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
        
        // Channels
        for (const [patternName, patternData] of Object.entries(schedule)) {
            for (const channel of patternData.service_channels || []) {
                if (channel.programs && channel.programs.length > 0) {
                    lines.push(`  <channel id="${escapeXml(channel.id)}">`);
                    lines.push(`    <display-name>${escapeXml(channel.channel_name)}</display-name>`);
                    if (channel.icon_url) {
                        lines.push(`    <icon src="${escapeXml(channel.icon_url)}"/>`);
                    }
                    lines.push('  </channel>');
                }
            }
        }
        
        // Programs
        for (const [patternName, patternData] of Object.entries(schedule)) {
            for (const channel of patternData.service_channels || []) {
                for (const program of channel.programs || []) {
                    lines.push(`  <programme channel="${escapeXml(channel.id)}" start="${program.start_str}" stop="${program.stop_str}">`);
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
        const channelId = streamMatch[1];
        console.log(`[STREAM] Looking up channel: ${channelId}`);
        
        const schedule = await loadSchedule();
        const { channel, event } = findChannelAndEvent(schedule, channelId);
        
        if (!channel) {
            console.error(`[STREAM] Channel ${channelId} not found`);
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            res.end('Channel not found');
            return;
        }
        
        if (!event || !event.stream_url) {
            console.error(`[STREAM] No active event for ${channelId}`);
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            res.end('No active event');
            return;
        }
        
        console.log(`[STREAM] Found event: ${event.program_name}`);
        
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
        streamTS(event.stream_url, res, channelId, event.program_name);
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