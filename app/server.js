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
            
            // Follow the redirect
            proxyReq.destroy();
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
        
        proxyRes.on('data', (chunk) => {
            buffer = Buffer.concat([buffer, chunk]);
            
            // Process complete TS packets
            while (buffer.length >= TS_PACKET_SIZE) {
                // Find sync byte (0x47)
                const syncIdx = buffer.indexOf(0x47);
                
                if (syncIdx === -1) {
                    console.warn('[STREAM] No sync byte found, discarding buffer');
                    buffer = Buffer.alloc(0);
                    break;
                }
                
                if (syncIdx > 0) {
                    console.warn(`[STREAM] Discarding ${syncIdx} bytes before sync`);
                    buffer = buffer.slice(syncIdx);
                }
                
                if (buffer.length < TS_PACKET_SIZE) {
                    break;
                }
                
                // Extract one TS packet
                const packet = buffer.slice(0, TS_PACKET_SIZE);
                buffer = buffer.slice(TS_PACKET_SIZE);
                
                packetCount++;
                bytesSent += packet.length;
                
                if (packetCount === 1) {
                    console.log('[STREAM] First packet sent');
                }
                
                if (packetCount % 5000 === 0) {
                    const mb = (bytesSent / (1024 * 1024)).toFixed(1);
                    console.log(`[STREAM] ${mb}MB sent (${packetCount} packets)`);
                }
                
                // Write packet to response
                if (!res.write(packet)) {
                    // Backpressure - pause upstream
                    proxyRes.pause();
                    res.once('drain', () => {
                        proxyRes.resume();
                    });
                }
            }
        });
        
        proxyRes.on('end', () => {
            // Flush remaining complete packets
            while (buffer.length >= TS_PACKET_SIZE) {
                const packet = buffer.slice(0, TS_PACKET_SIZE);
                buffer = buffer.slice(TS_PACKET_SIZE);
                bytesSent += packet.length;
                res.write(packet);
            }
            
            const mb = (bytesSent / (1024 * 1024)).toFixed(2);
            console.log(`[STREAM] Stream ended: ${mb}MB`);
            res.end();
        });
        
        proxyRes.on('error', (error) => {
            console.error(`[STREAM] Proxy response error:`, error.message);
            if (!res.headersSent) {
                res.writeHead(500, { 'Content-Type': 'text/plain' });
            }
            res.end('Stream error: ' + error.message);
        });
    });
    
    proxyReq.on('error', (error) => {
        console.error(`[STREAM] Proxy request error:`, error.message);
        console.error(`[STREAM] Error details:`, error);
        if (!res.headersSent) {
            res.writeHead(502, { 'Content-Type': 'text/plain' });
        }
        res.end('Connection error: ' + error.message);
    });
    
    proxyReq.on('timeout', () => {
        console.error('[STREAM] Request timeout');
        proxyReq.destroy();
        if (!res.headersSent) {
            res.writeHead(504, { 'Content-Type': 'text/plain' });
        }
        res.end('Gateway timeout');
    });
    
    proxyReq.end();
    
    // Handle client disconnect
    res.on('close', () => {
        const mb = (bytesSent / (1024 * 1024)).toFixed(2);
        console.log(`[STREAM] Client disconnected after ${mb}MB`);
        proxyReq.destroy();
    });
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
    
    // Stream endpoint
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
            console.error(`[STREAM] Channel has ${channel.programs ? channel.programs.length : 0} programs`);
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            res.end('No active event');
            return;
        }
        
        console.log(`[STREAM] Found event: ${event.program_name}`);
        console.log(`[STREAM] Stream URL: ${event.stream_url}`);
        
        // Set headers BEFORE streaming starts - matches working IPTV provider
        res.writeHead(200, {
            'Date': new Date().toUTCString(),
            'Content-Type': 'video/mp2t',
            'Content-Length': '0',
            'Connection': 'keep-alive',
            'Pragma': 'public',
            'Cache-Control': 'public, must-revalidate, proxy-revalidate'
        });
        
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