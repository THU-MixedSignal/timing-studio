"""Exercise the actual packaged entrypoint from an unrelated directory."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'timing-studio'
CLI = SKILL / 'scripts' / 'timing_studio.py'

class DistributionTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / '.cache'
        cache.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='distribution-', dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)

    def call(self, *args):
        return subprocess.run([sys.executable, '-I', '-X', 'utf8', '-B', str(CLI), *map(str, args)],
                              cwd=self.cwd, capture_output=True, text=True, encoding='utf-8', timeout=20)

    def test_readonly_doctor_resolves_bundled_runtime(self):
        before = sorted(str(p) for p in self.cwd.rglob('*'))
        result = self.call('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn(str(SKILL / 'scripts' / 'runtime').replace('\\', '/'), result.stdout.replace('\\\\', '/'))
        self.assertNotIn('token', report)
        self.assertEqual(before, sorted(str(p) for p in self.cwd.rglob('*')))

    def test_explicit_session_required(self):
        result = self.call('launch', '--no-open')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--session', result.stderr)

    def test_demo_and_high_speed_validate(self):
        candidate = self.cwd / '中文 demo.timing.json'
        result = self.call('demo', '--output', candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in [candidate, ROOT / 'examples' / 'high-speed-800ps.timing.json']:
            result = self.call('validate', '--project', path)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_files_match_bytes(self):
        for base, name in [(ROOT, 'package-manifest.json'), (SKILL, 'install-manifest.json')]:
            manifest = json.loads((base / name).read_text('utf-8'))
            for item in manifest['files']:
                path = (base / item['path']).resolve()
                self.assertTrue(path.is_relative_to(base.resolve()))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item['sha256'], item['path'])

    def test_invalid_window_view_is_rejected_before_launch(self):
        project = json.loads((ROOT / 'examples' / 'high-speed-800ps.timing.json').read_text('utf-8'))
        project['view']['signal_order'] = ['missing_signal']
        candidate = self.cwd / 'invalid-view.json'
        candidate.write_text(json.dumps(project), encoding='utf-8')
        before = candidate.read_bytes()
        result = self.call('validate', '--project', candidate)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['valid'])
        self.assertEqual(candidate.read_bytes(), before)

    def test_only_local_assets(self):
        static = SKILL / 'scripts' / 'runtime' / 'local_window' / 'static'
        for path in static.iterdir():
            text = path.read_text('utf-8')
            self.assertNotIn('https://', text)
            self.assertNotIn('cdn.', text)
