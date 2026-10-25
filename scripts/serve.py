"""Preview server for site/: like `python -m http.server --directory site`, but never cached by the browser."""
import functools
import http.server
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "site"


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    handler = functools.partial(NoCache, directory=str(ROOT))
    print(f"http://localhost:{port}")
    http.server.ThreadingHTTPServer(("", port), handler).serve_forever()
