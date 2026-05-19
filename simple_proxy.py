#!/usr/bin/env python3
"""
Simple proxy server to redirect news.mininglifeserver.com to localhost:8000
Run this on port 8889
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.parse

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.proxy_request()
    
    def do_POST(self):
        self.proxy_request()
    
    def do_PUT(self):
        self.proxy_request()
    
    def do_DELETE(self):
        self.proxy_request()
    
    def proxy_request(self):
        # Target server (your FastAPI app)
        target_url = f"http://127.0.0.1:8000{self.path}"
        
        try:
            # Forward the request
            req = urllib.request.Request(
                target_url,
                method=self.command,
                headers=dict(self.headers)
            )
            
            # Add request body for POST/PUT requests
            if self.command in ['POST', 'PUT']:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    req.data = self.rfile.read(content_length)
            
            # Make the request
            with urllib.request.urlopen(req) as response:
                # Send response headers
                self.send_response(response.status)
                for header, value in response.headers.items():
                    self.send_header(header, value)
                self.end_headers()
                
                # Send response body
                self.wfile.write(response.read())
                
        except Exception as e:
            self.send_error(500, f"Proxy error: {str(e)}")
    
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

if __name__ == "__main__":
    server = HTTPServer(('0.0.0.0', 8889), ProxyHandler)
    print("Proxy server running on port 8889")
    print("Access your API at: http://news.mininglifeserver.com:8889")
    print("Make sure to add '127.0.0.1 news.mininglifeserver.com' to /etc/hosts")
    server.serve_forever()
