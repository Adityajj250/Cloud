"""
R.I.F.T. Mobile Bridge & Test Server
Runs locally on port 8080.
Allows Android phones on the same Wi-Fi or across WAN (via hotspot/ngrok/AWS)
to access the R.I.F.T. mobile command center instantly from Chrome or installed PWA.
"""

import http.server
import socketserver
import os
import socket

PORT = 8080
DIRECTORY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pwa")

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("=" * 60)
    print(">>> R.I.F.T. ANDROID MOBILE COMMAND SERVER ACTIVE <<<")
    print("=" * 60)
    print(f"  * Open on this machine:  http://localhost:{PORT}")
    print(f"  * Open on your Android phone: http://{local_ip}:{PORT}")
    print("  * Tips for Android phone:")
    print("    1. Open Chrome on your Android phone and navigate to the IP above.")
    print("    2. Tap Chrome's menu (three dots) -> 'Install App' or 'Add to Home Screen'.")
    print("    3. It installs as a full standalone Android app with push & camera access.")
    print("=" * 60)
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
