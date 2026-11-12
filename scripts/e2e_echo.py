"""Tiny upstream for the Caddy experiment: answers every request with the headers it received.

Runs inside a throwaway container as ``backend:8000`` behind the real web image, so the e2e test
can observe exactly what Caddy forwards (X-Forwarded-For, Authorization, …).
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Echo(BaseHTTPRequestHandler):
    def _reply(self) -> None:
        body = json.dumps(
            {
                "peer": self.client_address[0],
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _reply

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Echo).serve_forever()  # noqa: S104
