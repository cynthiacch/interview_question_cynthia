"""Local development server - mirrors the Vercel layout.

Serves public/ as static files and routes the POST endpoints to the same
code Vercel runs. Standard library only: `python3 server.py`.
"""

import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from web import calculate, convert_units, plot, respond, units

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
POST_ROUTES = {"/api/calculate": calculate, "/api/graph": plot, "/api/convert": convert_units}
GET_ROUTES = {"/api/units": units}


class LocalHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    def do_GET(self):
        endpoint = GET_ROUTES.get(self.path.split("?")[0])
        if endpoint:
            respond(self, endpoint)
        else:
            super().do_GET()

    def do_POST(self):
        endpoint = POST_ROUTES.get(self.path)
        if endpoint:
            respond(self, endpoint)
        else:
            self.send_error(404)


def main():
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), LocalHandler)
    print(f"Calculator running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
