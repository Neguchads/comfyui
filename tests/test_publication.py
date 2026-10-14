import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))
from check_publication import inspect
import pc_tools


class PublicationTests(unittest.TestCase):
    def test_unknown_file_rejected(self):
        self.assertIn('FILE_NOT_ALLOWED', inspect('photo.png', b'image'))

    def test_sensitive_pattern_rejected(self):
        generated = ('gh' + 'p_' + 'A' * 36).encode()
        self.assertIn('provider_token', inspect('README.md', generated))

    def test_binary_rejected(self):
        self.assertIn('NOT_UTF8_TEXT', inspect('README.md', b'\xff\xfe'))

    def test_template_reference_and_graph(self):
        workflow = json.loads((ROOT / 'workflows/poseclone-qwen21.json').read_text(encoding='utf-8'))
        graph = pc_tools.graph(workflow)
        self.assertEqual(graph['errors'], [])
        load = next(n for n in workflow['nodes'] if n['type'] == 'LoadImage')
        self.assertEqual(load['widgets_values'][0], 'reference.png')
        self.assertEqual(load['widgets_values_named']['image'], 'reference.png')
        studio = next(n for n in workflow['nodes'] if n['type'] == 'VNCCS_PoseStudio')
        scene = json.loads(studio['widgets_values'][0])
        self.assertEqual(scene['capture_id'], '')
        self.assertIsNone(scene['background_url'])
        self.assertIn(scene['active_character_id'], [x['id'] for x in scene['characters']])

    def test_named_widgets_match_array(self):
        workflow = json.loads((ROOT / 'workflows/poseclone-qwen21.json').read_text(encoding='utf-8'))
        for node in workflow['nodes']:
            named = node.get('widgets_values_named', {})
            if named:
                self.assertEqual(list(named.values()), node['widgets_values'], node['type'])


if __name__ == '__main__':
    unittest.main()
