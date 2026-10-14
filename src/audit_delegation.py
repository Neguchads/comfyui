"""Auditoria local de chamadas reais; não exporta mensagens da conversa."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def timestamp_value(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def audit(root):
    root = Path(root)
    calls, returns, sessions, origins = {}, set(), {}, Counter()
    session_details, lifecycle_calls, seen_events = {}, {}, set()
    files = malformed = missing_id = conflicts = source_bytes = 0
    output_bytes = 0
    for path in sorted(Path(root).rglob('*.jsonl')):
        current_session = None
        files += 1
        source_bytes += path.stat().st_size
        with path.open(encoding='utf-8-sig', errors='replace') as stream:
            for number, line in enumerate(stream, 1):
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    malformed += 1
                    continue
                if not isinstance(event, dict):
                    malformed += 1
                    continue
                payload = event.get('payload')
                if not isinstance(payload, dict):
                    continue
                if event.get('type') == 'session_meta':
                    sid = payload.get('id')
                    current_session = sid if isinstance(sid, str) else None
                    if isinstance(sid, str) and sid not in sessions:
                        source = payload.get('source', 'unknown')
                        origin = json.dumps(source, ensure_ascii=False, sort_keys=True)
                        sessions[sid] = origin
                        origins[origin] += 1
                        child = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) and isinstance(source.get('subagent'), dict) else {}
                        if not isinstance(child, dict):
                            child = {}
                        session_details[sid] = {'session_id': sid,
                            'parent_thread_id': child.get('parent_thread_id') if isinstance(child.get('parent_thread_id'), str) else None,
                            'agent_path': child.get('agent_path') if isinstance(child.get('agent_path'), str) else None,
                            'agent_role': child.get('agent_role') if isinstance(child.get('agent_role'), str) else None,
                            'events': {}, 'latest_task_state': None, 'token_usage': None,
                            'token_timestamp': None, 'evidence': str(path.relative_to(root)) + ':' + str(number)}
                if event.get('type') == 'event_msg' and current_session in session_details:
                    detail = session_details[current_session]
                    etype = payload.get('type')
                    timestamp = str(event.get('timestamp', ''))
                    numeric_timestamp = timestamp_value(event.get('timestamp'))
                    marker = (current_session, timestamp, hashlib.sha256(
                        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest())
                    if marker not in seen_events:
                        seen_events.add(marker)
                        if etype in ('task_started', 'task_complete', 'turn_aborted', 'error'):
                            detail['events'][etype] = detail['events'].get(etype, 0) + 1
                            state = {'task_started': 'started', 'task_complete': 'complete',
                                     'turn_aborted': 'aborted', 'error': 'error'}[etype]
                            if numeric_timestamp is not None and numeric_timestamp >= detail.get('_state_time', float('-inf')):
                                detail['latest_task_state'] = state
                                detail['state_timestamp'] = timestamp
                                detail['_state_time'] = numeric_timestamp
                    info = payload.get('info')
                    total = info.get('total_token_usage') if isinstance(info, dict) else None
                    if etype == 'token_count' and isinstance(total, dict) and numeric_timestamp is not None and numeric_timestamp >= detail.get('_token_time', float('-inf')):
                        detail['token_timestamp'] = timestamp
                        detail['_token_time'] = numeric_timestamp
                        detail['token_usage'] = {key: value if type(value) is int and value >= 0 else None
                            for key in ('input_tokens', 'cached_input_tokens', 'output_tokens',
                                        'reasoning_output_tokens', 'total_tokens')
                            for value in [total.get(key)]}
                if event.get('type') != 'response_item':
                    continue
                kind = payload.get('type')
                cid = payload.get('call_id')
                if kind == 'function_call_output':
                    if isinstance(cid, str):
                        returns.add(cid)
                    output_bytes += len(json.dumps(payload.get('output'), ensure_ascii=False).encode('utf-8'))
                    continue
                name = payload.get('name', '')
                if kind != 'function_call':
                    continue
                if not isinstance(name, str):
                    malformed += 1
                    continue
                operation = name.rsplit('.', 1)[-1]
                if operation in ('wait_agent', 'send_message', 'followup_task', 'interrupt_agent', 'list_agents'):
                    if name not in (operation, 'collaboration.' + operation, 'functions.collaboration.' + operation):
                        continue
                    if isinstance(cid, str) and cid not in lifecycle_calls:
                        args = payload.get('arguments', {})
                        try:
                            args = json.loads(args) if isinstance(args, str) else args
                        except ValueError:
                            args = {}
                        lifecycle_calls[cid] = {'call_id': cid, 'operation': operation,
                            'parent_session_id': current_session,
                            'target': args.get('target') if isinstance(args, dict) else None,
                            'evidence': str(path.relative_to(root)) + ':' + str(number)}
                    continue
                if name not in {
                    'spawn_agent', 'collaboration.spawn_agent',
                    'functions.collaboration.spawn_agent',
                }:
                    continue
                if not isinstance(cid, str) or not cid:
                    missing_id += 1
                    continue
                args = payload.get('arguments', {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = None
                if not isinstance(args, dict):
                    malformed += 1
                    continue
                # Mensagem enviada ao agente nunca entra no resultado.
                entry = {'call_id': cid, 'agent_type': args.get('agent_type', 'default'),
                         'task_name': args.get('task_name'),
                         'parent_session_id': current_session,
                         'fork_turns': args.get('fork_turns', 'all'),
                         'evidence': str(path.relative_to(root)) + ':' + str(number)}
                if cid in calls:
                    if any(calls[cid][key] != entry[key] for key in ('agent_type', 'task_name', 'fork_turns')):
                        conflicts += 1
                else:
                    calls[cid] = entry
    paths = Counter()
    for entry in calls.values():
        parent = session_details.get(entry['parent_session_id'], {})
        task = entry['task_name']
        entry['agent_path'] = (task if task.startswith('/') else (parent.get('agent_path') or '/root') + '/' + task) if isinstance(task, str) else None
        paths[(entry['parent_session_id'], entry['agent_path'])] += 1
    for entry in calls.values():
        entry['tool_return_seen'] = entry['call_id'] in returns
        agent_path = entry['agent_path']
        children = [detail for detail in session_details.values()
                    if detail['parent_thread_id'] == entry['parent_session_id'] and detail['agent_path'] == agent_path]
        unambiguous = agent_path is not None and len(children) == 1 and paths[(entry['parent_session_id'], agent_path)] == 1
        entry['child_match'] = 'UNIQUE_METADATA_MATCH' if unambiguous else ('AMBIGUOUS' if children else 'NOT_FOUND')
        entry['candidate_child_session_ids'] = [detail['session_id'] for detail in children]
        children = children if unambiguous else []
        entry['child_session_ids'] = [detail['session_id'] for detail in children]
        entry['child_states_observed'] = {detail['session_id']: detail['latest_task_state'] for detail in children}
    for entry in lifecycle_calls.values():
        entry['tool_return_seen'] = entry['call_id'] in returns
    for detail in session_details.values():
        detail.pop('_token_time', None)
        detail.pop('_state_time', None)
    return {'snapshot_utc': datetime.now(timezone.utc).isoformat(),
            'files': files, 'unique_session_ids': len(sessions),
            'origins': dict(origins), 'unique_spawn_calls': len(calls),
            'by_agent_type': dict(Counter(str(x['agent_type']) for x in calls.values())),
            'by_fork_turns': dict(Counter(str(x['fork_turns']) for x in calls.values())),
            'spawn_calls_with_tool_return': sum(x['tool_return_seen'] for x in calls.values()),
            'malformed_records': malformed, 'spawn_without_call_id': missing_id,
            'duplicate_conflicts': conflicts, 'source_bytes': source_bytes,
            'serialized_tool_output_bytes': output_bytes, 'calls': list(calls.values()),
            'lifecycle_calls': list(lifecycle_calls.values()),
            'lifecycle_counts': dict(Counter(x['operation'] for x in lifecycle_calls.values())),
            'session_details': list(session_details.values()),
            'semantic_quality': 'REVIEW_REQUIRED: adequação, redundância de trabalho, retorno suficiente e incorporação exigem revisão de evidências; não inferidos automaticamente.',
            'limits': 'Tool return não comprova conclusão, revisão ou adequação. Estado filho usa eventos nativos observados, não aceitação pelo principal. Tokens são último contador por session_meta.id; não somar como uso da conta ou atribuir a um único tool. Bytes de respostas incluem registros duplicados e não são tokens. Arquivos ativos podem ter linhas incompletas.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions', type=Path, default=Path.home() / '.codex' / 'sessions')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not args.sessions.is_dir():
        parser.error('Diretório de sessões inexistente')
    result = audit(args.sessions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {key: result[key] for key in (
        'files', 'unique_session_ids', 'unique_spawn_calls', 'by_agent_type',
        'by_fork_turns', 'spawn_calls_with_tool_return', 'malformed_records',
        'spawn_without_call_id', 'duplicate_conflicts', 'source_bytes')}
    summary['lifecycle_counts'] = result['lifecycle_counts']
    summary['linked_child_sessions'] = sum(len(x['child_session_ids']) for x in result['calls'])
    summary['sessions_with_token_usage'] = sum(x['token_usage'] is not None for x in result['session_details'])
    summary['report'] = str(args.report.resolve())
    summary['quality_review_required'] = True
    summary['stdout_bytes'] = 0
    while True:
        output = json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
        size = len((output + '\n').encode('utf-8'))
        if summary['stdout_bytes'] == size:
            break
        summary['stdout_bytes'] = size
    # Saída UTF-8 determinística inclusive no PowerShell.
    import sys
    sys.stdout.buffer.write((output + '\n').encode('utf-8'))


if __name__ == '__main__':
    main()
