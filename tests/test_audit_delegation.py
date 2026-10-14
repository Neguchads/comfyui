import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import json
from pathlib import Path
import tempfile
import unittest
from audit_delegation import audit


class AuditTests(unittest.TestCase):
    def run_audit(self, records):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'session.jsonl').write_text('\n'.join(
                json.dumps(x) if not isinstance(x, str) else x for x in records), encoding='utf-8')
            return audit(root)

    def call(self, cid='one', **args):
        return {'type': 'response_item', 'payload': {'type': 'function_call',
                'name': 'collaboration.spawn_agent', 'call_id': cid,
                'arguments': json.dumps({'task_name': 'review', **args})}}

    def test_deduplicates_real_calls(self):
        result = self.run_audit([self.call(), self.call()])
        self.assertEqual(result['unique_spawn_calls'], 1)

    def test_text_mentions_do_not_count(self):
        result = self.run_audit([{'type': 'response_item', 'payload': {
            'type': 'message', 'text': 'spawn_agent agent_type mapeador-de-disco'}}])
        self.assertEqual(result['unique_spawn_calls'], 0)

    def test_call_return_is_not_completion(self):
        result = self.run_audit([self.call(), {'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'call_id': 'one', 'output': 'created'}}])
        self.assertEqual(result['spawn_calls_with_tool_return'], 1)
        self.assertNotIn('completed', result['calls'][0])

    def test_defaults_and_no_prompt_leak(self):
        result = self.run_audit([self.call(message='PRIVATE_CONTENT')])
        self.assertEqual(result['by_agent_type'], {'default': 1})
        self.assertEqual(result['by_fork_turns'], {'all': 1})
        self.assertNotIn('PRIVATE_CONTENT', json.dumps(result))

    def test_active_partial_line_reported(self):
        result = self.run_audit([self.call(fork_turns='none'), '{incomplete'])
        self.assertEqual(result['malformed_records'], 1)
        self.assertEqual(result['unique_spawn_calls'], 1)

    def test_sessions_dedup_and_origins(self):
        meta = {'type': 'session_meta', 'payload': {'id': 's1', 'source': 'vscode'}}
        result = self.run_audit([meta, meta, {'type': 'session_meta', 'payload': {
            'id': 's2', 'source': {'subagent': {'other': 'guardian'}}}}])
        self.assertEqual(result['unique_session_ids'], 2)
        self.assertEqual(sum(result['origins'].values()), 2)

    def test_missing_id_not_counted(self):
        result = self.run_audit([self.call(cid=None)])
        self.assertEqual(result['spawn_without_call_id'], 1)
        self.assertEqual(result['unique_spawn_calls'], 0)

    def test_invalid_function_names_reported(self):
        records = [self.call(), self.call()]
        records[0]['payload']['name'] = {}
        records[1]['payload']['name'] = []
        result = self.run_audit(records)
        self.assertEqual(result['malformed_records'], 2)
        self.assertEqual(result['unique_spawn_calls'], 0)

    def test_parent_child_link_and_completion(self):
        result = self.run_audit([
            {'type': 'session_meta', 'payload': {'id': 'parent', 'source': 'vscode'}}, self.call(),
            {'type': 'session_meta', 'payload': {'id': 'child', 'source': {'subagent': {'thread_spawn': {
                'parent_thread_id': 'parent', 'agent_path': '/root/review', 'agent_role': 'default'}}}}},
            {'type': 'event_msg', 'timestamp': '2026-10-14T01:00:00Z', 'payload': {'type': 'task_complete', 'last_agent_message': 'PRIVATE'}}])
        self.assertEqual(result['calls'][0]['child_session_ids'], ['child'])
        self.assertEqual(result['calls'][0]['child_states_observed'], {'child': 'complete'})
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_token_counters_last_snapshot_not_sum(self):
        meta = {'type': 'session_meta', 'payload': {'id': 'p', 'source': 'vscode'}}
        records = [meta]
        for timestamp, count in [('2026-01-02', 20), ('2026-01-01', 10)]:
            records.append({'type': 'event_msg', 'timestamp': timestamp, 'payload': {'type': 'token_count',
                'info': {'total_token_usage': {'input_tokens': count, 'output_tokens': 3}}}})
        result = self.run_audit(records)
        self.assertEqual(result['session_details'][0]['token_usage']['input_tokens'], 20)
        self.assertIsNone(result['session_details'][0]['token_usage']['cached_input_tokens'])

    def test_lifecycle_calls_deduplicated(self):
        item = {'type': 'response_item', 'payload': {'type': 'function_call',
            'name': 'collaboration.send_message', 'call_id': 'send',
            'arguments': json.dumps({'target': 'review', 'message': 'PRIVATE'})}}
        result = self.run_audit([item, item])
        self.assertEqual(result['lifecycle_counts'], {'send_message': 1})
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_restarted_child_is_not_complete(self):
        result = self.run_audit([
            {'type': 'session_meta', 'payload': {'id': 'c'}},
            {'type': 'event_msg', 'timestamp': '2026-01-01', 'payload': {'type': 'task_complete'}},
            {'type': 'event_msg', 'timestamp': '2026-01-02', 'payload': {'type': 'task_started'}}])
        self.assertEqual(result['session_details'][0]['latest_task_state'], 'started')

    def test_fractional_timestamp_is_newer(self):
        records = [{'type': 'session_meta', 'payload': {'id': 'p'}}]
        for timestamp, kind, count in [('2026-01-01T00:00:00Z', 'task_complete', 10),
                                       ('2026-01-01T00:00:00.100Z', 'task_started', 20)]:
            records.append({'type': 'event_msg', 'timestamp': timestamp, 'payload': {'type': kind}})
            records.append({'type': 'event_msg', 'timestamp': timestamp, 'payload': {'type': 'token_count',
                'info': {'total_token_usage': {'input_tokens': count}}}})
        result = self.run_audit(records)
        self.assertEqual(result['session_details'][0]['latest_task_state'], 'started')
        self.assertEqual(result['session_details'][0]['token_usage']['input_tokens'], 20)

    def test_reused_task_name_is_ambiguous(self):
        result = self.run_audit([
            {'type': 'session_meta', 'payload': {'id': 'parent'}}, self.call(cid='one'), self.call(cid='two'),
            {'type': 'session_meta', 'payload': {'id': 'child', 'source': {'subagent': {'thread_spawn': {
                'parent_thread_id': 'parent', 'agent_path': '/root/review'}}}}}])
        for entry in result['calls']:
            self.assertEqual(entry['child_match'], 'AMBIGUOUS')
            self.assertEqual(entry['child_session_ids'], [])

    def test_unrelated_tool_suffix_not_counted(self):
        item = self.call()
        item['payload']['name'] = 'unrelated.send_message'
        result = self.run_audit([item])
        self.assertEqual(result['lifecycle_counts'], {})

    def test_invalid_parent_agent_path_does_not_crash(self):
        result = self.run_audit([
            {'type': 'session_meta', 'payload': {'id': 'parent', 'source': {'subagent': {'thread_spawn': {
                'parent_thread_id': 'other', 'agent_path': []}}}}}, self.call()])
        self.assertEqual(result['unique_spawn_calls'], 1)


if __name__ == '__main__':
    unittest.main()
