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

    def test_validated_prompt_and_output_prefix(self):
        # Garante o alinhamento com o prompt operacional validado e a pasta de saída.
        workflow = json.loads((ROOT / 'workflows/poseclone-qwen21.json').read_text(encoding='utf-8'))
        save = next(n for n in workflow['nodes'] if n['type'] == 'SaveImage')
        self.assertEqual(save['widgets_values'][0], 'Imagens/Qwen_PoseClone')
        self.assertEqual(save['widgets_values_named']['filename_prefix'], 'Imagens/Qwen_PoseClone')
        encoder = next(n for n in workflow['nodes'] if n['type'] == 'TextEncodeQwenImage21')
        prompt = encoder['widgets_values_named']['prompt']
        self.assertEqual(prompt, encoder['widgets_values'][0])
        self.assertIn('Draw character from image2', prompt)
        self.assertIn('Repose the person in image2 to match image1', prompt)
        seeds = [n['widgets_values_named']['seed'] for n in workflow['nodes']
                 if 'seed' in n.get('widgets_values_named', {})]
        self.assertEqual(seeds, [42])

    def test_posestudio_v2_lora(self):
        # PoseStudio V2 é o LoRA atual do VNCCS v3.0 para Qwen Image 2.1, no layout oficial de pastas.
        workflow = json.loads((ROOT / 'workflows/poseclone-qwen21.json').read_text(encoding='utf-8'))
        loras = [n for n in workflow['nodes'] if n['type'] == 'LoraLoaderModelOnly']
        self.assertEqual(len(loras), 1)
        expected = 'QI2.1\\VNCCS\\VNCCS_QI2_PoseStudioV2.safetensors'
        self.assertEqual(loras[0]['widgets_values'][0], expected)
        self.assertEqual(loras[0]['widgets_values_named']['lora_name'], expected)
        self.assertNotIn('PoseStudioV1.1', (ROOT / 'workflows/poseclone-qwen21.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
