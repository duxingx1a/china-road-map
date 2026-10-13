"""Loopback-only UI fixture: hold one route until /qa/release is requested."""
import argparse
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Handler, LocalServer

GATE = threading.Event()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18768)
    parser.add_argument('--route', default='national-G219')
    args = parser.parse_args()

    class FixtureHandler(Handler):
        def handle_GET(self):
            path = urlsplit(self.path).path
            if path == '/qa/release':
                GATE.set()
                return self.json({'released': True})
            if path == f'/data/routes/{args.route}.geojson':
                GATE.wait(timeout=600)
            return super().handle_GET()

    server = LocalServer(('127.0.0.1', args.port), FixtureHandler)
    print(f'Loading fixture: http://127.0.0.1:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        GATE.set()
        server.server_close()


if __name__ == '__main__':
    main()
