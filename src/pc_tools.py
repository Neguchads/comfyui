"""Diagnósticos locais compactos para PoseClone; somente biblioteca padrão."""
import argparse
from collections import Counter, deque
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = Path(__file__).resolve().parent
COMFY = Path(os.environ.get('POSECLONE_COMFY_DIR', str(BASE.parent.parent / 'ComfyUI' / 'ComfyUI'))).resolve()
URL = 'http://127.0.0.1:8188'
VERSION = 1


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_bytes(encoded(value))
    os.replace(temp, path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTP_REDIRECT_BLOCKED')


class HttpFailure(ValueError):
    def __init__(self, code, report):
        super().__init__('HTTP_' + str(code))
        self.report = str(report)


def http(route, payload=None):
    # Destino fixo, sem proxy, redirecionamento, autenticação ou descoberta remota.
    if not (route in ('/system_stats', '/queue', '/object_info', '/prompt') or
            re.fullmatch(r'/history/[a-zA-Z0-9-]+', route)):
        raise ValueError('ENDPOINT_NOT_ALLOWED')
    if payload is not None and route != '/prompt':
        raise ValueError('WRITE_NOT_ALLOWED')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(URL + route, data=encoded(payload) if payload is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        report = BASE / 'reports' / ('http-error-' + uuid.uuid4().hex + '.json')
        body = exc.read().decode('utf-8', errors='replace')
        write(report, {'status_code': exc.code, 'endpoint': route, 'body': body})
        raise HttpFailure(exc.code, report) from None


def catalog():
    # Consulta fresca: modelos instalados durante a sessão podem mudar os enums.
    data = http('/object_info')
    write(BASE / 'cache/node_catalog.json', data)
    return data


def file_inventory(root):
    result = {}
    ignored = {'.git', '__pycache__', 'node_modules', '.venv'}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ignored and not Path(directory, d).is_symlink())
        for name in sorted(files):
            p = Path(directory, name)
            if p.is_symlink() or name.startswith('.env'):
                continue
            s = p.stat()
            result[str(p.relative_to(root)).replace('\\', '/')] = [s.st_size, s.st_mtime_ns]
    return result


def inventory():
    files = {str(COMFY / folder): file_inventory(COMFY / folder)
             for folder in ('models', 'custom_nodes', 'user/default/workflows')}
    key = digest({'version': VERSION, 'files': files})
    cache = BASE / 'cache/inventory.json'
    old = read(cache) if cache.exists() else {}
    counts = {Path(k).name: len(v) for k, v in files.items()}
    changes = {}
    for root, entries in files.items():
        prev = old.get('files', {}).get(root, {})
        changes[root] = {'added': sorted(entries.keys() - prev.keys()),
                         'removed': sorted(prev.keys() - entries.keys()),
                         'changed': sorted(k for k in entries.keys() & prev.keys() if entries[k] != prev[k])}
    full = {'key': key, 'files': files, 'changes': changes, 'counts': counts,
            'cache_hit': old.get('key') == key, 'fingerprint': 'path+size+mtime_ns; not content hash'}
    write(cache, {'key': key, 'files': files})
    return full, {'counts': counts, 'cache_hit': full['cache_hit'], 'fingerprint': key,
                  'changes': {Path(k).name: {n: len(v) for n, v in d.items()} for k, d in changes.items()}}


def graph(data):
    if not isinstance(data, dict):
        raise ValueError('WORKFLOW_NOT_OBJECT')
    if 'prompt' in data and isinstance(data['prompt'], dict):
        data = data['prompt']
    edges, errors = [], []
    if isinstance(data.get('nodes'), list):
        nodes = {}
        for n in data['nodes']:
            key = str(n['id'])
            if key in nodes:
                errors.append({'code': 'DUPLICATE_NODE', 'node': key})
            nodes[key] = {'type': n['type'], 'inputs': n.get('inputs', []),
                          'widgets': n.get('widgets_values', []), 'mode': n.get('mode', 0)}
        for link in data.get('links', []):
            if isinstance(link, list) and len(link) >= 6:
                _, src, slot, target, field, kind = link[:6]
            elif isinstance(link, dict):
                src, slot, target, field, kind = (link[k] for k in
                    ('origin_id', 'origin_slot', 'target_id', 'target_slot', 'type'))
            else:
                raise ValueError('UNSUPPORTED_UI_LINK')
            edges.append([str(src), slot, str(target), field, kind])
        fmt = 'ui'
        if data.get('definitions', {}).get('subgraphs'):
            errors.append({'code': 'SUBGRAPHS_REQUIRE_API_EXPORT'})
    else:
        nodes = {}
        for key, n in data.items():
            if not isinstance(n, dict) or 'class_type' not in n or not isinstance(n.get('inputs'), dict):
                raise ValueError('INVALID_API_NODE')
            nodes[str(key)] = {'type': n['class_type'], 'inputs': n['inputs']}
            for field, value in n['inputs'].items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int:
                    edges.append([value[0], value[1], str(key), field, None])
        fmt = 'api'
    if not nodes:
        errors.append({'code': 'EMPTY_GRAPH'})
    for src, slot, dst, field, kind in edges:
        if src not in nodes or dst not in nodes:
            errors.append({'code': 'DANGLING_LINK', 'source': src, 'target': dst})
        if type(slot) is not int or slot < 0:
            errors.append({'code': 'INVALID_SLOT', 'source': src})
    return {'format': fmt, 'nodes': nodes, 'edges': edges, 'errors': errors,
            'subgraph_ids': {str(s['id']) for s in data.get('definitions', {}).get('subgraphs', [])}}


def validate_graph(g, cat=None):
    incomplete = bool(g.get('subgraph_ids'))
    errors = [e for e in g['errors'] if e['code'] != 'SUBGRAPHS_REQUIRE_API_EXPORT']
    nodes = g['nodes']
    active = set(nodes)
    if cat is not None and g['format'] == 'api':
        outputs = {k for k, n in nodes.items() if cat.get(n['type'], {}).get('output_node')}
        if outputs:
            active = set(outputs)
            pending = deque(outputs)
            while pending:
                target = pending.popleft()
                for src, _, dst, _, _ in g['edges']:
                    if dst == target and src in nodes and src not in active:
                        active.add(src)
                        pending.append(src)
    # Kahn detecta ciclos sem recursão em grafos grandes.
    degree = {k: 0 for k in nodes}
    downstream = {k: [] for k in nodes}
    for src, slot, dst, field, kind in g['edges']:
        if src in nodes and dst in nodes:
            degree[dst] += 1
            downstream[src].append(dst)
    queue = deque(k for k, v in degree.items() if v == 0)
    seen = 0
    while queue:
        key = queue.popleft()
        seen += 1
        for dst in downstream[key]:
            degree[dst] -= 1
            if degree[dst] == 0:
                queue.append(dst)
    if seen != len(nodes):
        errors.append({'code': 'CYCLE'})
    if cat is not None:
        for key, node in nodes.items():
            schema = cat.get(node['type'])
            if schema is None:
                if node['type'] not in ('Reroute', 'Note', 'MarkdownNote') and node['type'] not in g.get('subgraph_ids', set()):
                    errors.append({'code': 'UNKNOWN_NODE', 'node': key, 'type': node['type']})
                continue
            if g['format'] != 'api':
                continue
            if key not in active:
                continue
            required = schema.get('input', {}).get('required', {})
            for field, spec in required.items():
                if spec[0] == 'COMFY_AUTOGROW_V3':
                    template = spec[1].get('template', {})
                    fields = [f for f in node['inputs'] if f.startswith(field + '.')]
                    names = template.get('names')
                    if len(fields) < template.get('min', 0) or len(fields) > template.get('max', len(fields)):
                        errors.append({'code': 'AUTOGROW_COUNT', 'node': key, 'field': field})
                    if names and any(f.split('.', 1)[1] not in names for f in fields):
                        errors.append({'code': 'AUTOGROW_NAME', 'node': key, 'field': field})
                    continue
                if field not in node['inputs']:
                    errors.append({'code': 'MISSING_INPUT', 'node': key, 'field': field})
            specs = dict(required, **schema.get('input', {}).get('optional', {}))
            linked = {e[3] for e in g['edges'] if e[2] == key}
            for field, value in node['inputs'].items():
                if field in linked or field not in specs:
                    continue
                spec = specs[field]
                if isinstance(spec[0], list) and value not in spec[0]:
                    errors.append({'code': 'ENUM_OR_MODEL_MISSING', 'node': key, 'field': field})
        for src, slot, dst, field, kind in g['edges']:
            if src not in nodes or dst not in nodes or type(slot) is not int or slot < 0:
                continue
            source_schema = cat.get(nodes[src]['type'], {})
            if 'output' in source_schema and slot >= len(source_schema['output']):
                errors.append({'code': 'OUTPUT_SLOT_OUT_OF_RANGE', 'node': src, 'slot': slot})
    return {'status': 'FAIL' if errors else ('INCOMPLETE' if incomplete else 'PASS'), 'scope': 'static_preflight',
            'format': g['format'], 'nodes': len(nodes), 'edges': len(g['edges']), 'errors': errors,
            'live_catalog': cat is not None,
            'requires_api_export': incomplete,
            'inactive_nodes': len(nodes) - len(active),
            'limits': 'No execution, custom VALIDATE_INPUTS, widget conversion or visual quality proof.'}


def trace(g, pattern, source=None, target=None):
    selected = {k for k, n in g['nodes'].items() if re.search(pattern, n['type'], re.I)}
    reach = set()
    if source:
        if source not in g['nodes']:
            raise ValueError('SOURCE_NODE_NOT_FOUND')
        reach.add(source)
        queue = deque([source])
        while queue:
            key = queue.popleft()
            for src, _, dst, _, _ in g['edges']:
                if src == key and dst not in reach:
                    reach.add(dst)
                    queue.append(dst)
    relevant = selected | reach
    return {'status': 'FAIL' if source and target and target not in reach else 'INSPECT',
            'nodes': {k: n for k, n in g['nodes'].items() if k in relevant},
            'edges': [e for e in g['edges'] if e[0] in relevant or e[2] in relevant],
            'source': source, 'target': target, 'reachable': target in reach if source and target else None,
            'limits': 'Graph reachability does not prove pose selection, editor state or image fidelity.'}


def graph_diff(a, b):
    changes = []
    for key in sorted(a['nodes'].keys() | b['nodes'].keys()):
        old, new = a['nodes'].get(key), b['nodes'].get(key)
        if old != new:
            changes.append({'node': key, 'before': old, 'after': new})
    old_edges = {tuple(x) for x in a['edges']}
    new_edges = {tuple(x) for x in b['edges']}
    return {'nodes_changed': changes, 'edges_added': sorted(new_edges - old_edges, key=str),
            'edges_removed': sorted(old_edges - new_edges, key=str)}


def cached_analysis(command, data, options, compute):
    # Conteúdo do workflow + opções + versão do próprio código, sem confiar no nome do arquivo.
    key = digest({'data': data, 'options': options,
                  'code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    path = BASE / 'cache' / (command + '-' + key + '.json')
    hit = path.exists()
    result = read(path) if hit else compute()
    if not hit:
        write(path, result)
    return dict(result, cache_hit=hit)


def api_snapshot(path, data):
    index_path = BASE / 'cache/export_index.json'
    if not index_path.exists():
        return data, None
    entry = read(index_path).get(str(Path(path).resolve()))
    if not entry or entry['source_sha256'] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
        return data, None
    export = (BASE / entry['api_path']).resolve()
    if not export.is_relative_to(BASE) or not export.is_file():
        return data, None
    if hashlib.sha256(export.read_bytes()).hexdigest() != entry['api_sha256']:
        return data, None
    return read(export), {'validation_source': 'verified_frontend_api_snapshot',
                          'api_snapshot': str(export), 'original_hash_matched': True}


def token_usage(path, mark=None, baseline=None):
    path = Path(path).resolve()
    if not path.is_relative_to(Path.home() / '.codex/sessions') and not path.is_relative_to(BASE):
        raise ValueError('SESSION_PATH_NOT_ALLOWED')
    latest = None
    events = 0
    # Lê somente eventos de uso. Não devolve texto de conversas ou ferramentas.
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if '"token_count"' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # O arquivo pode estar recebendo uma linha nova.
            payload = event.get('payload', {})
            if payload.get('type') == 'token_count' and payload.get('info'):
                latest = {'timestamp': event.get('timestamp'), 'info': payload['info']}
                events += 1
    if latest is None:
        raise ValueError('TOKEN_TELEMETRY_UNAVAILABLE')
    keys = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
    total = {k: latest['info'].get('total_token_usage', {}).get(k) for k in keys}
    last = {k: latest['info'].get('last_token_usage', {}).get(k) for k in keys}
    result = {'status': 'MEASURED', 'timestamp': latest['timestamp'], 'events': events,
              'total': total, 'last_request': last,
              'limits': 'Native session counters; not invoice, A/B savings or attribution to one tool.'}
    if total['input_tokens'] is not None and total['cached_input_tokens'] is not None:
        result['uncached_input_tokens'] = total['input_tokens'] - total['cached_input_tokens']
    session = hashlib.sha256(str(path).encode()).hexdigest()
    for label in (mark, baseline):
        if label and not re.fullmatch('[a-zA-Z0-9_-]{1,48}', label):
            raise ValueError('INVALID_MARK')
    if baseline:
        previous = read(BASE / 'cache' / ('tokens-' + baseline + '.json'))
        if previous['session'] != session:
            raise ValueError('DIFFERENT_SESSION_BASELINE')
        delta = {k: total[k] - previous['total'][k] if total[k] is not None and previous['total'].get(k) is not None else None for k in keys}
        if any(v is not None and v < 0 for v in delta.values()):
            raise ValueError('COUNTERS_RESET')
        result['delta_since_mark'] = delta
    if mark:
        marker = BASE / 'cache' / ('tokens-' + mark + '.json')
        if marker.exists():
            raise ValueError('MARK_EXISTS')
        write(marker, {'session': session, 'total': total, 'timestamp': latest['timestamp']})
        result['mark'] = mark
    return result


def summarize_logs(path):
    counts = Counter()
    lines = 0
    # Categorias, sem copiar prompts, rostos, tokens ou texto pessoal dos logs.
    patterns = {'cuda_oom': r'out of memory|CUDA.*memory', 'face_not_detected': r'no face detected',
                'missing_module': r'ModuleNotFoundError|ImportError', 'error': r'error|exception|traceback',
                'warning': r'warning', 'completed': r'Prompt executed in'}
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            lines += 1
            for category, pattern in patterns.items():
                if re.search(pattern, line, re.I):
                    counts[category] += 1
    return {'lines': lines, 'categories': dict(counts), 'source_bytes': Path(path).stat().st_size,
            'limits': 'Categories overlap; original log remains on disk.'}


def execute_workflow(path, allow, timeout):
    if not allow:
        raise ValueError('RUN_REQUIRES_EXECUTE_LOCAL')
    data = read(path)
    g = graph(data)
    if g['format'] != 'api':
        raise ValueError('EXPORT_API_FORMAT_FIRST')
    cat = catalog()
    check = validate_graph(g, cat)
    if check['status'] != 'PASS':
        return check
    # Lista positiva: execução de nodes desconhecidos ou de rede não é implicitamente autorizada.
    safe = {'CheckpointLoaderSimple', 'CLIPTextEncode', 'EmptyLatentImage', 'KSampler',
            'VAEDecode', 'SaveImage', 'PreviewImage', 'LoadImage', 'ImageScale', 'ImageScaleBy',
            'ControlNetLoader', 'ControlNetApplyAdvanced', 'VAELoader', 'VAEEncode'}
    blocked = sorted({n['type'] for n in g['nodes'].values()} - safe)
    if blocked:
        return {'status': 'BLOCKED', 'reason': 'NODE_NOT_REVIEWED_FOR_LOCAL_EXECUTION', 'types': blocked}
    q = http('/queue')
    if q.get('queue_running') or q.get('queue_pending'):
        return {'status': 'BLOCKED', 'reason': 'QUEUE_BUSY'}
    prompt = data.get('prompt', data)
    start = time.monotonic()
    response = http('/prompt', {'prompt': prompt, 'client_id': 'poseclone-token-saver'})
    pid = response.get('prompt_id')
    if not pid or response.get('node_errors'):
        return {'status': 'FAIL', 'response': response}
    write(BASE / 'reports' / ('job-' + pid + '.json'), {'prompt_id': pid, 'workflow_sha256': digest(prompt)})
    while time.monotonic() - start < timeout:
        result = http('/history/' + pid)
        if pid in result:
            entry = result[pid]
            state = entry.get('status', {})
            if state.get('completed') or state.get('status_str') == 'error':
                return {'status': 'PASS' if state.get('status_str') == 'success' else 'FAIL',
                        'prompt_id': pid, 'elapsed_s': round(time.monotonic() - start, 3),
                        'workflow_sha256': digest(prompt), 'history': entry,
                        'quality': 'not_evaluated'}
        time.sleep(1)
    return {'status': 'TIMEOUT', 'prompt_id': pid, 'job_may_continue': True}


def emit(command, full, summary, source_bytes=0):
    report = BASE / 'reports' / (command + '-' + uuid.uuid4().hex[:12] + '.json')
    write(report, full)
    envelope = {'command': command, 'result': summary, 'report': str(report)}
    if len(encoded(envelope)) > 3200:
        envelope['result'] = {'status': full.get('status', 'INSPECT'), 'details_on_disk': True,
                              'errors_count': len(full.get('errors', []))}
    output = encoded(envelope)
    measurement = {'command': command, 'stdout_bytes': len(output) + 1,
                   'report_bytes': report.stat().st_size, 'source_bytes': source_bytes,
                   'tokens': None, 'time_unix': time.time()}
    with (BASE / 'reports/metrics.jsonl').open('ab') as f:
        f.write(encoded(measurement) + b'\n')
    sys.stdout.buffer.write(output + b'\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory')
    sub.add_parser('status')
    sub.add_parser('catalog')
    t = sub.add_parser('tokens')
    t.add_argument('path')
    t.add_argument('--mark')
    t.add_argument('--baseline')
    for name in ('validate', 'trace', 'diff', 'logs', 'run'):
        s = sub.add_parser(name)
        s.add_argument('path')
        if name == 'validate':
            s.add_argument('--live', action='store_true')
        if name == 'trace':
            s.add_argument('--pattern', default='pose|controlnet|vnccs|depth')
            s.add_argument('--source')
            s.add_argument('--target')
        if name == 'diff':
            s.add_argument('other')
        if name == 'run':
            s.add_argument('--execute-local', action='store_true')
            s.add_argument('--timeout', type=int, default=300)
    args = p.parse_args()
    source_bytes = 0
    try:
        source_bytes = Path(args.path).stat().st_size if hasattr(args, 'path') else 0
        if args.command == 'inventory':
            full, short = inventory()
            source_bytes = len(encoded(full['files']))
        elif args.command == 'status':
            stats, q = http('/system_stats'), http('/queue')
            full = {'stats': stats, 'running': len(q.get('queue_running', [])), 'pending': len(q.get('queue_pending', []))}
            short = {'version': stats.get('system', {}).get('comfyui_version'), 'devices': stats.get('devices'),
                     'running': full['running'], 'pending': full['pending']}
        elif args.command == 'catalog':
            full = catalog()
            groups = {'vnccs': 'vnccs', 'controlnet': 'controlnet', 'faceid': 'faceid',
                      'instantid': 'instantid', 'pulid': 'pulid', 'ipadapter': 'ipadapter'}
            short = {'nodes': len(full), 'registered': {k: sum(bool(re.search(v, n, re.I)) for n in full) for k, v in groups.items()}}
            source_bytes = len(encoded(full))
        elif args.command == 'logs':
            full = short = summarize_logs(args.path)
        elif args.command == 'tokens':
            full = short = token_usage(args.path, args.mark, args.baseline)
        elif args.command == 'run':
            full = execute_workflow(args.path, args.execute_local, args.timeout)
            short = {k: v for k, v in full.items() if k != 'history'}
            if 'history' in full:
                short['output_nodes'] = list(full['history'].get('outputs', {}))
        else:
            data = read(args.path)
            g = graph(data)
            if args.command == 'validate':
                provenance = None
                if g['subgraph_ids']:
                    data, provenance = api_snapshot(args.path, data)
                    g = graph(data)
                cat = catalog() if args.live else None
                full = cached_analysis('validate', data, {'catalog': digest(cat)}, lambda: validate_graph(g, cat))
                if provenance:
                    full.update(provenance)
                short = dict(full, errors=full['errors'][:8], errors_count=len(full['errors']))
            elif args.command == 'trace':
                full = cached_analysis('trace', data, vars(args), lambda: trace(g, args.pattern, args.source, args.target))
                short = {k: v for k, v in full.items() if k not in ('nodes', 'edges')}
                short.update(nodes={k: n['type'] for k, n in full['nodes'].items()}, edges_count=len(full['edges']))
            else:
                full = graph_diff(g, graph(read(args.other)))
                source_bytes += Path(args.other).stat().st_size
                short = {'changed_nodes': [x['node'] for x in full['nodes_changed']],
                         'edges_added': len(full['edges_added']), 'edges_removed': len(full['edges_removed'])}
        emit(args.command, full, short, source_bytes)
        return 1 if full.get('status') in ('FAIL', 'BLOCKED', 'TIMEOUT', 'INCOMPLETE') else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Não devolver corpos HTTP nem conteúdo de arquivos potencialmente sensíveis.
        detail = {'status': 'ERROR', 'error_type': type(exc).__name__,
                  'code': str(exc) if re.fullmatch('[A-Z_0-9]+', str(exc)) else 'INPUT_OR_CONNECTION_ERROR'}
        if isinstance(exc, HttpFailure):
            detail['diagnostic_report'] = exc.report
        emit(args.command, detail, detail, source_bytes)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
