import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pc_tools as pc


def sample():
    return {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'example.png'}},
            '2': {'class_type': 'PreviewImage', 'inputs': {'images': ['1', 0]}}}


class ToolTests(unittest.TestCase):
    def test_api_reachability(self):
        g = pc.graph(sample())
        self.assertTrue(pc.trace(g, 'load', '1', '2')['reachable'])
        self.assertFalse(pc.trace(g, 'load', '2', '1')['reachable'])

    def test_ui_links_and_modes(self):
        g = pc.graph({'nodes': [{'id': 1, 'type': 'VNCCS', 'mode': 4},
                                 {'id': 2, 'type': 'PreviewImage'}], 'links': [[1, 1, 0, 2, 0, 'IMAGE']]})
        self.assertEqual(g['nodes']['1']['mode'], 4)
        self.assertTrue(pc.trace(g, 'vnccs', '1', '2')['reachable'])

    def test_dangling_link(self):
        data = sample()
        del data['1']
        self.assertEqual(pc.validate_graph(pc.graph(data))['status'], 'FAIL')

    def test_subgraph_is_incomplete_not_missing_custom_node(self):
        g = pc.graph({'nodes': [{'id': 1, 'type': 'subgraph-id'}], 'links': [],
                      'definitions': {'subgraphs': [{'id': 'subgraph-id'}]}})
        result = pc.validate_graph(g, {})
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['errors'], [])

    def test_autogrow_and_inactive_nodes(self):
        data = {'1': {'class_type': 'Encoder', 'inputs': {'images.image_1': ['2', 0]}},
                '2': {'class_type': 'LoadImage', 'inputs': {'image': 'x.png'}},
                '3': {'class_type': 'Encoder', 'inputs': {}},
                '4': {'class_type': 'PreviewImage', 'inputs': {'images': ['1', 0]}}}
        cat = {'Encoder': {'input': {'required': {'images': ['COMFY_AUTOGROW_V3', {'template': {'min': 1, 'names': ['image_1']}}]}}},
               'LoadImage': {}, 'PreviewImage': {'output_node': True}}
        result = pc.validate_graph(pc.graph(data), cat)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['inactive_nodes'], 1)
        data['1']['inputs'] = {}
        self.assertEqual(pc.validate_graph(pc.graph(data), cat)['status'], 'FAIL')

    def test_native_tokens_dedup_and_delta(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch.object(pc, 'BASE', Path(tmp)):
            path = Path(tmp) / 'session.jsonl'
            def event(total):
                return json.dumps({'timestamp': 'now', 'payload': {'type': 'token_count', 'info': {'total_token_usage': total, 'last_token_usage': total}}}) + '\n'
            total = {'input_tokens': 100, 'cached_input_tokens': 70, 'output_tokens': 10, 'reasoning_output_tokens': 2, 'total_tokens': 110}
            path.write_text(event(total) * 2, encoding='utf-8')
            result = pc.token_usage(path, mark='before')
            self.assertEqual(result['total']['input_tokens'], 100)
            self.assertEqual(result['uncached_input_tokens'], 30)
            total['input_tokens'] = 130
            path.write_text(event(total), encoding='utf-8')
            self.assertEqual(pc.token_usage(path, baseline='before')['delta_since_mark']['input_tokens'], 30)

    def test_export_snapshot_requires_matching_hashes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch.object(pc, 'BASE', Path(tmp)):
            source = Path(tmp) / 'source.json'
            export = Path(tmp) / 'export.json'
            pc.write(source, {'nodes': []})
            pc.write(export, sample())
            pc.write(Path(tmp) / 'cache/export_index.json', {str(source.resolve()): {
                'source_sha256': pc.hashlib.sha256(source.read_bytes()).hexdigest(),
                'api_sha256': pc.hashlib.sha256(export.read_bytes()).hexdigest(), 'api_path': 'export.json'}})
            data, provenance = pc.api_snapshot(source, pc.read(source))
            self.assertEqual(data, sample())
            self.assertTrue(provenance['original_hash_matched'])
            pc.write(source, {'nodes': [{'id': 1}]})
            self.assertIsNone(pc.api_snapshot(source, pc.read(source))[1])

    def test_cycle(self):
        data = sample()
        data['1']['inputs']['image'] = ['2', 0]
        self.assertIn({'code': 'CYCLE'}, pc.validate_graph(pc.graph(data))['errors'])

    def test_model_enum_and_missing_input(self):
        cat = {'LoadImage': {'input': {'required': {'image': [['other.png']], 'required_value': ['INT']}}},
               'PreviewImage': {'input': {'required': {'images': ['IMAGE']}}}}
        codes = {e['code'] for e in pc.validate_graph(pc.graph(sample()), cat)['errors']}
        self.assertEqual(codes, {'ENUM_OR_MODEL_MISSING', 'MISSING_INPUT'})

    def test_diff_ignores_layout_detects_parameter_and_link(self):
        a = sample()
        b = copy.deepcopy(a)
        b['1']['inputs']['image'] = 'new.png'
        d = pc.graph_diff(pc.graph(a), pc.graph(b))
        self.assertEqual([n['node'] for n in d['nodes_changed']], ['1'])
        self.assertEqual(d['edges_added'], [])

    def test_empty_and_unsupported(self):
        self.assertEqual(pc.validate_graph(pc.graph({}))['status'], 'FAIL')
        with self.assertRaises(ValueError):
            pc.graph({'not_a_workflow': 4})

    def test_cache_content_and_options_invalidation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch.object(pc, 'BASE', Path(tmp)):
            fn = lambda: {'status': 'PASS'}
            self.assertFalse(pc.cached_analysis('test', sample(), {}, fn)['cache_hit'])
            self.assertTrue(pc.cached_analysis('test', sample(), {}, fn)['cache_hit'])
            self.assertFalse(pc.cached_analysis('test', sample(), {'live': True}, fn)['cache_hit'])
            data = sample()
            data['1']['inputs']['image'] = 'changed.png'
            self.assertFalse(pc.cached_analysis('test', data, {}, fn)['cache_hit'])

    def test_inventory_change_removal(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            base = Path(tmp)
            (base / 'models').mkdir()
            model = base / 'models/test.bin'
            model.write_bytes(b'1')
            with patch.object(pc, 'BASE', base / 'tool'), patch.object(pc, 'COMFY', base):
                self.assertFalse(pc.inventory()[0]['cache_hit'])
                self.assertTrue(pc.inventory()[0]['cache_hit'])
                model.write_bytes(b'12')
                self.assertFalse(pc.inventory()[0]['cache_hit'])
                model.unlink()
                self.assertEqual(pc.inventory()[1]['changes']['models']['removed'], 1)

    def test_logs_no_sensitive_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            log = Path(tmp) / 'test.log'
            log.write_text('ERROR api_key=secret-personal-value\nCUDA out of memory\n', encoding='utf-8')
            result = pc.summarize_logs(log)
            self.assertEqual(result['categories']['cuda_oom'], 1)
            self.assertNotIn('secret', json.dumps(result))

    def test_bounded_output_metrics_match(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch.object(pc, 'BASE', Path(tmp)):
            stream = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
            with contextlib.redirect_stdout(stream):
                pc.emit('test', {'errors': ['x'] * 5000}, {'errors': ['x'] * 5000})
            output = stream.buffer.getvalue()
            self.assertLess(len(output), 4096)
            measurement = json.loads((Path(tmp) / 'reports/metrics.jsonl').read_text())
            self.assertEqual(measurement['stdout_bytes'], len(output))
            self.assertEqual(measurement['tokens'], None)

    def test_execution_needs_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'RUN_REQUIRES'):
            pc.execute_workflow('unused', False, 1)

    def test_paid_node_blocked(self):
        data = {'1': {'class_type': 'PartnerPaidNode', 'inputs': {}}}
        with patch.object(pc, 'read', return_value=data), patch.object(pc, 'catalog', return_value={'PartnerPaidNode': {}}), patch.object(pc, 'http') as http:
            self.assertEqual(pc.execute_workflow('unused', True, 1)['status'], 'BLOCKED')
            http.assert_not_called()

    def test_busy_queue_no_submit(self):
        with patch.object(pc, 'read', return_value=sample()), patch.object(pc, 'catalog', return_value={'LoadImage': {}, 'PreviewImage': {}}), patch.object(pc, 'http', return_value={'queue_running': [1]}) as http:
            self.assertEqual(pc.execute_workflow('unused', True, 1)['reason'], 'QUEUE_BUSY')
            http.assert_called_once_with('/queue')

    def test_remote_endpoint_blocked(self):
        with self.assertRaisesRegex(ValueError, 'ENDPOINT_NOT_ALLOWED'):
            pc.http('https://example.com')

    def test_http_failure_keeps_details_local(self):
        error = pc.urllib.error.HTTPError(pc.URL + '/prompt', 400, 'Bad Request', {}, io.BytesIO(b'{"node_errors":{"1":"bad input"}}'))
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch.object(pc, 'BASE', Path(tmp)), patch.object(pc.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(pc.HttpFailure) as caught:
                pc.http('/prompt', {})
            self.assertEqual(str(caught.exception), 'HTTP_400')
            self.assertIn('node_errors', Path(caught.exception.report).read_text())


if __name__ == '__main__':
    unittest.main()
