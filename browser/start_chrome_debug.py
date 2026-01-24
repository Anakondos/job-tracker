#!/usr/bin/env python3
"""
Start Chrome with debug port and specific profile.
Handles existing Chrome instances.
"""

import subprocess
import socket
import time
import os
import signal
from pathlib import Path

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEBUG_PORT = 9222
CHROME_USER_DATA = str(Path.home() / "Library/Application Support/Google/Chrome")
PROFILE = "Default"  # anakondos@gmail.com


def is_port_open(port: int) -> bool:
    """Check if port is in use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(('localhost', port)) == 0
    except:
        return False


def kill_chrome():
    """Kill all Chrome processes."""
    try:
        subprocess.run(['pkill', '-9', 'Google Chrome'], capture_output=True)
        time.sleep(1)
    except:
        pass


def start_chrome_debug(kill_existing: bool = True) -> dict:
    """
    Start Chrome with debug port.
    
    Args:
        kill_existing: If True, kill existing Chrome before starting
        
    Returns:
        {"ok": bool, "message": str, "already_running": bool}
    """
    
    # Check if already running with debug
    if is_port_open(DEBUG_PORT):
        return {
            "ok": True,
            "message": f"Chrome already running with debug on port {DEBUG_PORT}",
            "already_running": True
        }
    
    # Kill existing Chrome if requested
    if kill_existing:
        kill_chrome()
        time.sleep(1)
    
    # Start Chrome with debug port
    try:
        cmd = [
            CHROME_PATH,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={CHROME_USER_DATA}",
            f"--profile-directory={PROFILE}",
        ]
        
        # Start in background
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        # Wait for Chrome to start
        for _ in range(20):  # 10 seconds max
            time.sleep(0.5)
            if is_port_open(DEBUG_PORT):
                return {
                    "ok": True,
                    "message": f"Chrome started with debug on port {DEBUG_PORT}",
                    "already_running": False,
                    "pid": process.pid
                }
        
        return {
            "ok": False,
            "message": "Chrome started but debug port not available",
            "already_running": False
        }
        
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed to start Chrome: {e}",
            "already_running": False
        }


if __name__ == "__main__":
    import sys
    
    kill = "--no-kill" not in sys.argv
    result = start_chrome_debug(kill_existing=kill)
    
    print(f"{'✅' if result['ok'] else '❌'} {result['message']}")
    
    if result['ok']:
        print(f"\nChrome ready for V5 Form Filler!")
        print(f"Debug port: {DEBUG_PORT}")
        print(f"Profile: {PROFILE} (anakondos@gmail.com)")
