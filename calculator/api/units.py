"""Vercel serverless function for GET /api/units (see web.py for the API)."""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import respond, units  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        respond(self, units)
