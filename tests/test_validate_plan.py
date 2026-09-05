import json, subprocess, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).parents[1]
class PlanTest(unittest.TestCase):
  def runplan(self, obj):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'p.json'; o=Path(d)/'o.json'; p.write_text(json.dumps(obj))
      return subprocess.run(['python3',str(ROOT/'controller/validate_plan.py'),str(p),'--output',str(o)],capture_output=True)
  def test_valid(self):
    x=json.loads((ROOT/'plans/cp2k.example.json').read_text()); self.assertEqual(self.runplan(x).returncode,0)
  def test_unknown_partition(self):
    x=json.loads((ROOT/'plans/cp2k.example.json').read_text()); x['targets'][0]['partition']='nope'; self.assertNotEqual(self.runplan(x).returncode,0)
  def test_path_escape(self):
    x=json.loads((ROOT/'plans/cp2k.example.json').read_text()); x['targets'][0]['install_prefix']='../opt'; self.assertNotEqual(self.runplan(x).returncode,0)
