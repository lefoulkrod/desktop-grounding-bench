#!/usr/bin/env python3
"""Replay frozen desktop screenshots against Ollama; never sends desktop input."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import time
import urllib.request

from grounding import locate_target, targeting_prompt

ROOT = Path(__file__).resolve().parent
ABSENT = 'Click the button labelled ZEBRA-ABSENT-9042.'


def load_fixtures(path=ROOT / 'fixtures/manifest.json'):
    fixtures = json.loads(path.read_text())
    for fixture in fixtures:
        image = path.parent / fixture['image']
        data = image.read_bytes()
        if data[:8] != b'\x89PNG\r\n\x1a\n' or list(struct.unpack('>II', data[16:24])) != fixture['size']:
            raise ValueError(f"Invalid PNG/dimensions: {image}")
        if hashlib.sha256(data).hexdigest() != fixture['sha256']:
            raise ValueError(f"Image checksum mismatch: {image}")
        fixture['image_path'] = image
    return fixtures


def invoke(model, image, target, host, timeout=120):
    payload = {'model': model, 'messages': [{'role': 'user', 'content': targeting_prompt(target),
               'images': [base64.b64encode(image).decode()]}], 'stream': False,
               'think': False, 'options': {'temperature': 0, 'num_predict': 4096}}
    request = urllib.request.Request(host.rstrip('/') + '/api/chat',
                                    data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    elapsed = time.monotonic() - started
    if result.get('error'):
        raise RuntimeError(result['error'])
    return result['message']['content'], elapsed


def score(fixture, response, absent=False):
    try:
        point = locate_target(response, *fixture['size'])
    except (ValueError, TypeError) as error:
        return {'hit': False, 'point': None, 'failure': 'contract', 'error': str(error)}
    if absent:
        return {'hit': point is None, 'point': point, 'failure': None if point is None else 'false_positive'}
    box = fixture['boxes'][str(fixture['target_ref'])]
    hit = point is not None and box['x'] <= point[0] < box['x'] + box['width'] and box['y'] <= point[1] < box['y'] + box['height']
    return {'hit': hit, 'point': point, 'failure': None if hit else 'target'}


def summarize(rows):
    result = {}
    for model in sorted({row['model'] for row in rows}):
        positive = [r for r in rows if r['model'] == model and not r['absent']]
        absent = [r for r in rows if r['model'] == model and r['absent']]
        times = [r['request_seconds'] for r in positive if r.get('request_seconds') is not None]
        result[model] = {'hits': sum(r['hit'] for r in positive), 'attempts': len(positive),
                         'median_request_seconds': statistics.median(times) if times else None,
                         'absent_correct': sum(r['hit'] for r in absent), 'absent_attempts': len(absent),
                         'failures': {kind: sum(r.get('failure') == kind for r in positive + absent)
                                      for kind in ['contract', 'target', 'false_positive', 'request']}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', action='append', help='Repeat to compare models; default deepseek-v4.1-flash:cloud')
    parser.add_argument('--host', default=os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434'))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int, help='Positive fixtures per run; omits absent cases (smoke test only)')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--output', type=Path, default=ROOT / 'runs' / time.strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--validate', action='store_true', help='Check fixture integrity without calling a model')
    args = parser.parse_args()
    if args.repeats < 1 or (args.limit is not None and args.limit < 1) or args.timeout <= 0:
        parser.error('repeats, limit and timeout must be positive')
    fixtures = load_fixtures()
    if args.validate:
        print(f'Validated {len(fixtures)} PNGs, dimensions and SHA-256 hashes.')
        return
    models = list(dict.fromkeys(args.model or ['deepseek-v4.1-flash:cloud']))
    host = args.host if '://' in args.host else 'http://' + args.host
    if args.output.exists():
        parser.error('output directory already exists; choose a new path to preserve prior runs')
    args.output.mkdir(parents=True)
    (args.output / 'metadata.json').write_text(json.dumps({
        'runner': 'standalone-extraction-v1', 'models': models, 'repeats': args.repeats,
        'limit': args.limit, 'think': False, 'temperature': 0, 'num_predict': 4096,
        'prompt_example': targeting_prompt(fixtures[0]['task']),
        'timing': 'HTTP request including provider/network overhead; not mouse-action time',
    }, indent=2) + '\n')
    cases = [(f, False) for f in fixtures[:args.limit]]
    if args.limit is None:
        cases += [(f, True) for f in [f for f in fixtures if f['split'] == 'native'][:6]]
    rows = []
    for run in range(1, args.repeats + 1):
        offset = (run - 1) % len(models)
        for model in models[offset:] + models[:offset]:
            for fixture, absent in cases:
                row = {'model': model, 'run': run, 'fixture': fixture['id'], 'split': fixture['split'], 'absent': absent}
                try:
                    response, elapsed = invoke(model, fixture['image_path'].read_bytes(),
                                               ABSENT if absent else fixture['task'], host, args.timeout)
                    row.update(response=response, request_seconds=elapsed, **score(fixture, response, absent))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                    row.update(hit=False, point=None, failure='request', error=str(error), request_seconds=None)
                rows.append(row)
                with (args.output / 'results.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
                (args.output / 'summary.json').write_text(json.dumps(summarize(rows), indent=2) + '\n')
                print(json.dumps({k: row.get(k) for k in ['model', 'run', 'fixture', 'absent', 'hit', 'failure', 'request_seconds']}), flush=True)
    print(json.dumps(summarize(rows), indent=2))


if __name__ == '__main__':
    main()
