"""Run the packaged standard-library tests, without a developer checkout."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'skills' / 'timing-studio' / 'scripts' / 'runtime'))
suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
