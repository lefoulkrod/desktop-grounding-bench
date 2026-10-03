import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import bench
from grounding import locate_target


class GroundingTests(unittest.TestCase):
    def test_fixtures_and_bounds(self):
        fixtures = bench.load_fixtures()
        self.assertEqual(len(fixtures), 60)
        for f in fixtures:
            b = f['boxes'][str(f['target_ref'])]
            response = json.dumps({'x': (b['x'] + b['width']/2)/(f['size'][0]-1)*1000,
                                   'y': (b['y'] + b['height']/2)/(f['size'][1]-1)*1000})
            self.assertTrue(bench.score(f, response)['hit'], f['id'])
            self.assertTrue(bench.score(f, '{"found":false}', absent=True)['hit'])
            self.assertFalse(bench.score(f, response, absent=True)['hit'])

    def test_reject_malformed_coordinates(self):
        for value in ['text {"x":2,"y":3}', '{"x":true,"y":1}', '{"x":NaN,"y":1}',
                      '{"x":1001,"y":1}', '{"x":-1,"y":1}', '{"x":1,"y":2,"why":"x"}', '[]']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                locate_target(value, 1280, 720)
        self.assertEqual(locate_target('```json\n{"x":1000,"y":1000}\n```', 1280, 720), (1279, 719))

    def test_http_contract(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"message":{"content":"{\\"x\\":500,\\"y\\":250}"}}')
            def log_message(self, *args): pass
        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                response, elapsed = bench.invoke('test', b'PNG', 'Save', f'http://127.0.0.1:{server.server_port}')
            finally:
                server.shutdown(); thread.join()
        self.assertEqual(locate_target(response, 1280, 720), (640, 180))
        self.assertFalse(captured[0]['think'])
        self.assertEqual(captured[0]['options'], {'temperature': 0, 'num_predict': 4096})
        self.assertEqual(captured[0]['messages'][0]['content'], bench.targeting_prompt('Save'))
        self.assertGreaterEqual(elapsed, 0)

    def test_historical_scores_reproduce(self):
        fixtures = {f['id']: f for f in bench.load_fixtures()}
        path = bench.ROOT/'results/historical/visual-tool-benchmark-results.json'
        rows = json.loads(path.read_text())
        for row in rows:
            self.assertEqual(bench.score(fixtures[row['fixture']], row.get('response') or '', row.get('absent', False))['hit'],
                             row['hit'], (row['model'], row['fixture'], row['run']))


if __name__ == '__main__':
    unittest.main()
