"""
Local dev proxy for Butterfly Effect UI.
Serves static files AND proxies /pods/*, /users/*, /public/* to api.lemma.work
so the browser never makes cross-origin requests (no CORS issues).
"""
import http.server
import urllib.request
import urllib.error
import subprocess
import json
import os
import sys

API_BASE = "https://api.lemma.work"
STATIC_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "butterfly-effect", "apps", "butterfly-ui"
)
PORT = 5173

# Paths that should be proxied to the remote API
PROXY_PREFIXES = ("/pods/", "/users/", "/public/sdk/", "/st/")


def get_token():
    """Get a fresh token from the CLI."""
    try:
        result = subprocess.run(
            ["lemma", "auth", "print-token"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"[proxy] Warning: could not get token: {e}")
        return None


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    token = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_ROOT, **kwargs)

    def _should_proxy(self):
        return any(self.path.startswith(p) for p in PROXY_PREFIXES)

    def _proxy(self, method="GET", body=None):
        url = API_BASE + self.path
        headers = {}

        # Forward content-type
        ct = self.headers.get("Content-Type")
        if ct:
            headers["Content-Type"] = ct

        # Attach bearer token
        if ProxyHandler.token:
            headers["Authorization"] = f"Bearer {ProxyHandler.token}"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                # Forward response headers
                for key, val in resp.getheaders():
                    if key.lower() not in ("transfer-encoding", "content-encoding", "connection"):
                        self.send_header(key, val)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as e:
            msg = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):
        if self._should_proxy():
            self._proxy("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self._should_proxy():
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            self._proxy("POST", body)
        else:
            self.send_response(405)
            self.end_headers()

    def do_PATCH(self):
        if self._should_proxy():
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            self._proxy("PATCH", body)
        else:
            self.send_response(405)
            self.end_headers()

    def do_DELETE(self):
        if self._should_proxy():
            self._proxy("DELETE")
        else:
            self.send_response(405)
            self.end_headers()

    def do_PUT(self):
        if self._should_proxy():
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            self._proxy("PUT", body)
        else:
            self.send_response(405)
            self.end_headers()

    def log_message(self, format, *args):
        prefix = "[PROXY]" if self._should_proxy() else "[FILE] "
        sys.stderr.write(f"{prefix} {format % args}\n")


if __name__ == "__main__":
    print(f"[dev-server] Fetching auth token...")
    ProxyHandler.token = get_token()
    if ProxyHandler.token:
        print(f"[dev-server] Token acquired (length={len(ProxyHandler.token)})")
    else:
        print(f"[dev-server] WARNING: No token. API calls will be unauthenticated.")

    print(f"[dev-server] Serving UI from: {STATIC_ROOT}")
    print(f"[dev-server] Proxying API to: {API_BASE}")
    print(f"[dev-server] Open: http://localhost:{PORT}/index.html")
    print(f"[dev-server] Press Ctrl+C to stop.\n")

    # Use ThreadingHTTPServer so a single hung connection doesn't block the whole proxy
    server = http.server.ThreadingHTTPServer(("", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dev-server] Stopped.")
