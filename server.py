"""Offline, standard-library-only server. Binds solely to this computer."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
import argparse
import gzip
import json
import mimetypes
import threading
import webbrowser
import sqlite3
import math
import re
import traceback

LOCAL = threading.local()


def database(path):
    connections = getattr(LOCAL, 'connections', None)
    if connections is None:
        connections = LOCAL.connections = {}
    key = str(path)
    if key not in connections:
        connections[key] = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    return connections[key]

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
DATA = ROOT / 'data'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.handle_GET()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            traceback.print_exc()
            self.respond(b'{"error":"Local resource read failed; see server log"}', 'application/json', status=503)
        finally:
            for connection in getattr(LOCAL, 'connections', {}).values():
                connection.close()
            LOCAL.connections = {}

    def handle_GET(self):
        request = urlsplit(self.path)
        uri = request.path
        if uri == '/api/map-status':
            archives = []
            for region in ('china','taiwan'):
                path = DATA / 'basemap' / f'{region}.json'
                if path.exists() and path.with_suffix('.mbtiles').exists():
                    archives.append(json.loads(path.read_text(encoding='utf-8')))
            return self.json({'basemapReady': bool(archives), 'complete': len(archives) == 2, 'archives': archives, 'preciseNetworkReady': (DATA / 'network.sqlite').exists()})
        if uri == '/api/network':
            query = parse_qs(request.query)
            return self.precise_network(query.get('bbox',[''])[0], query.get('kinds',['national,expressway,provincial'])[0],query.get('zoom',[None])[0])
        match = re.fullmatch(r'/tiles/(china|taiwan)/(\d+)/(\d+)/(\d+)\.pbf',uri)
        if match:
            region,z,x,y = match.groups()
            z,x,y = int(z),int(x),int(y)
            if z > 14 or x >= 1 << z or y >= 1 << z:
                self.send_error(400)
                return
            path = DATA / 'basemap' / f'{region}.mbtiles'
            compact = DATA / 'display' / f'{region}.mbtiles'
            if z<=11 and compact.exists():path=compact
            if not path.exists():
                return self.respond(b'', 'application/vnd.mapbox-vector-tile')
            row = database(path).execute('SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?',(z,x,(1 << z)-1-y)).fetchone()
            data = row[0] if row else b''
            encoded = data[:2] == b'\x1f\x8b'
            if encoded and 'gzip' not in self.headers.get('Accept-Encoding',''):
                data = gzip.decompress(data)
                encoded = False
            return self.respond(data, 'application/vnd.mapbox-vector-tile', encoded, cache='public, max-age=3600')
        if uri == '/api/health':
            ready = (DATA / 'report.json').exists()
            report = json.loads((DATA / 'report.json').read_text(encoding='utf-8')) if ready else {}
            return self.respond(json.dumps({'ready': ready, 'offline': True, **report}, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
        base = DATA if uri.startswith('/data/') else WEB
        relative = uri[6:] if base == DATA else uri.lstrip('/') or 'index.html'
        target = (base / unquote(relative)).resolve()
        if not target.is_relative_to(base.resolve()) or (base == DATA and ('raw' in target.relative_to(base).parts or target.suffix in ('.sqlite','.mbtiles'))):
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        mime = 'application/geo+json' if target.suffix == '.geojson' else mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if mime.startswith('text/') or mime in ('application/json', 'application/javascript', 'application/geo+json'):
            mime += '; charset=utf-8'
        return self.respond(target.read_bytes(), mime)

    def json(self, value):
        return self.respond(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'),'application/json; charset=utf-8')

    def precise_network(self, value, kinds, zoom=None):
        try:
            w,s,e,n = [float(x) for x in value.split(',')]
            if not all(math.isfinite(x) for x in (w,s,e,n)) or not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
                raise ValueError()
        except ValueError:
            self.send_error(400,'Invalid bounding box')
            return
        types = list(dict.fromkeys(kinds.split(',')))
        if not types or any(kind not in ('national','expressway','provincial','highspeed','railway') for kind in types):
            self.send_error(400,'Invalid road kinds')
            return
        path = DATA / 'network.sqlite'
        column='coordinates'
        if zoom is not None:
            try:
                zoom=int(zoom)
                if not 7<=zoom<=18:raise ValueError()
            except ValueError:
                self.send_error(400,'Invalid display zoom');return
            display=DATA/'display/network.sqlite'
            if display.exists():
                path=display;column='z7' if zoom<9 else 'z10' if zoom<12 else 'coordinates'
        if not path.exists():
            return self.json({'type':'FeatureCollection','features':[],'ready':False})
        placeholders = ','.join('?' for _ in types)
        rows = database(path).execute(f'SELECT s.route,s.code,s.kind,s.{column} FROM spatial r JOIN segments s ON s.id=r.id WHERE r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=? AND s.kind IN ({placeholders}) LIMIT 20001',(e,w,n,s,*types)).fetchall()
        grouped = {}
        for route,code,kind,coordinates in rows[:20000]:
            feature = grouped.setdefault(route,{'type':'Feature','properties':{'id':route,'code':code,'kind':kind},'geometry':{'type':'MultiLineString','coordinates':[]}})
            feature['geometry']['coordinates'].append(json.loads(coordinates))
        return self.json({'type':'FeatureCollection','features':list(grouped.values()),'truncated':len(rows)>20000,'ready':True})

    def respond(self, content, mime, gzip_encoded=False, cache='no-cache', status=200):
        compressed = gzip_encoded or ('gzip' in self.headers.get('Accept-Encoding', '') and len(content) > 1024)
        if compressed and not gzip_encoded:
            content = gzip.compress(content, compresslevel=3)
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass

    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ('200', '304'):
            super().log_message(fmt, *args)


class LocalServer(ThreadingHTTPServer):
    # A detailed viewport starts dozens of concurrent local tile requests.
    request_queue_size = 128
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--open', action='store_true')
    options = parser.parse_args()
    for port in range(options.port, options.port + 10):
        try:
            server = LocalServer(('127.0.0.1', port), Handler)
            break
        except OSError as exc:
            if port == options.port + 9:
                raise RuntimeError('No free local port') from exc
    url = f'http://127.0.0.1:{port}/'
    print(f'China Road Atlas: {url}', flush=True)
    print('Local files only. Ctrl+C to stop.', flush=True)
    if options.open:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
