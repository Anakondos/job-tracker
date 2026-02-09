"""
Live Form Fill Monitor - Watch form filling in real-time via browser

Usage:
    1. Start monitor server: python browser/live_monitor.py
    2. Open http://localhost:8765 in browser
    3. Run form filler - screenshots stream to browser automatically
"""

import asyncio
import base64
import json
import threading
import time
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import websockets

# Global state
connected_clients = set()
current_screenshot = None
status_message = "Waiting for form filler..."

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Form Fill Monitor</title>
    <style>
        body { 
            background: #1a1a2e; 
            color: white; 
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            margin: 0;
            padding: 20px;
        }
        h1 { color: #00d4ff; margin-bottom: 10px; }
        #status { 
            padding: 10px; 
            background: #16213e; 
            border-radius: 8px; 
            margin-bottom: 15px;
            font-size: 14px;
        }
        #screenshot { 
            max-width: 100%; 
            border: 2px solid #00d4ff; 
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,212,255,0.3);
        }
        .connected { color: #00ff88; }
        .waiting { color: #ffaa00; }
        #fps { position: fixed; top: 10px; right: 20px; color: #666; }
    </style>
</head>
<body>
    <h1>🎬 Live Form Fill Monitor</h1>
    <div id="status" class="waiting">Connecting...</div>
    <div id="fps"></div>
    <img id="screenshot" src="" alt="Waiting for screenshot...">
    
    <script>
        const ws = new WebSocket('ws://localhost:8766');
        const img = document.getElementById('screenshot');
        const status = document.getElementById('status');
        const fpsEl = document.getElementById('fps');
        let frameCount = 0;
        let lastTime = Date.now();
        
        ws.onopen = () => {
            status.textContent = '✅ Connected - Waiting for form filler...';
            status.className = 'connected';
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'screenshot') {
                img.src = 'data:image/png;base64,' + data.data;
                frameCount++;
            } else if (data.type === 'status') {
                status.textContent = data.message;
            }
            
            // FPS counter
            const now = Date.now();
            if (now - lastTime > 1000) {
                fpsEl.textContent = frameCount + ' fps';
                frameCount = 0;
                lastTime = now;
            }
        };
        
        ws.onclose = () => {
            status.textContent = '❌ Disconnected';
            status.className = 'waiting';
        };
    </script>
</body>
</html>
"""

async def websocket_handler(websocket, path):
    """Handle WebSocket connections."""
    connected_clients.add(websocket)
    print(f"Client connected. Total: {len(connected_clients)}")
    try:
        await websocket.wait_closed()
    finally:
        connected_clients.discard(websocket)
        print(f"Client disconnected. Total: {len(connected_clients)}")


async def broadcast_screenshot(screenshot_base64: str):
    """Send screenshot to all connected clients."""
    if connected_clients:
        message = json.dumps({"type": "screenshot", "data": screenshot_base64})
        await asyncio.gather(*[client.send(message) for client in connected_clients])


async def broadcast_status(message: str):
    """Send status message to all clients."""
    if connected_clients:
        msg = json.dumps({"type": "status", "message": message})
        await asyncio.gather(*[client.send(msg) for client in connected_clients])


class MonitorHTTPHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())


def run_http_server():
    server = HTTPServer(('localhost', 8765), MonitorHTTPHandler)
    print("📺 Monitor page: http://localhost:8765")
    server.serve_forever()


# Global event loop for external use
_loop = None
_ws_server = None


def start_monitor_server():
    """Start the monitor server (call from main thread)."""
    global _loop, _ws_server
    
    # HTTP server in thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # WebSocket server
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    
    async def start_ws():
        global _ws_server
        _ws_server = await websockets.serve(websocket_handler, "localhost", 8766)
        print("🔌 WebSocket server: ws://localhost:8766")
    
    _loop.run_until_complete(start_ws())
    
    # Run in background thread
    def run_loop():
        _loop.run_forever()
    
    ws_thread = threading.Thread(target=run_loop, daemon=True)
    ws_thread.start()
    
    print("\n✅ Live monitor ready!")
    print("   Open http://localhost:8765 in your browser")
    print("   Then run form filler with send_to_monitor=True\n")


def send_screenshot(page) -> bool:
    """Send current page screenshot to monitor (call from form filler)."""
    global _loop
    if not _loop or not connected_clients:
        return False
    
    try:
        screenshot_bytes = page.screenshot(type="png")
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        asyncio.run_coroutine_threadsafe(broadcast_screenshot(screenshot_b64), _loop)
        return True
    except Exception as e:
        print(f"Screenshot error: {e}")
        return False


def send_status(message: str):
    """Send status message to monitor."""
    global _loop
    if _loop:
        asyncio.run_coroutine_threadsafe(broadcast_status(message), _loop)


if __name__ == "__main__":
    print("="*60)
    print("🎬 Live Form Fill Monitor Server")
    print("="*60)
    
    start_monitor_server()
    
    print("\nPress Ctrl+C to stop...\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
