import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_lammps_api as api


def lookup(kind, identifier):
    return '''  int iKIND = modify->find_KIND(IDENTIFIER);
  if (iKIND < 0) {
    error->all(FLERR, "missing " + IDENTIFIER);
  }

  TYPE* KIND = modify->KIND[iKIND];
  if (!KIND) {
    error->all(FLERR, "invalid " + IDENTIFIER);
  }
  return KIND;
'''.replace('TYPE', kind.title()).replace('KIND', kind).replace('IDENTIFIER', identifier)


class ModifyAPITests(unittest.TestCase):
    def test_id_lookup_preserves_error_guards_and_is_idempotent(self):
        for kind, name in (('compute', 'compute_fparam_id'), ('compute', 'compute_aparam_id'),
                           ('compute', 'source_id'), ('fix', 'fix_fparam_id'), ('fix', 'source_id')):
            with self.subTest(kind=kind, name=name):
                result, counts = api.adapt(lookup(kind, name))
                self.assertEqual(counts[kind], 1)
                self.assertIn('get_' + kind + '_by_id(' + name + ')', result)
                self.assertIn('error->all(FLERR, "missing " + ' + name + ');', result)
                self.assertEqual(api.adapt(result)[0], result)

    def test_unknown_access_fails_before_changing_either_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            header = root / 'modify.h'
            header.write_text('Fix *get_fix_by_id(const std::string &);\n'
                              'Compute *get_compute_by_id(const std::string &);\n')
            first, second = (root / name for name in api.FILES)
            original = lookup('fix', 'source_id')
            first.write_text(original)
            second.write_text('auto unknown = modify->fix[index + 1];\n')
            with self.assertRaisesRegex(ValueError, 'unrecognized legacy'):
                api.apply(header, root)
            self.assertEqual(first.read_text(), original)
            second.write_text(lookup('compute', 'source_id'))
            record = api.apply(header, root)
            for path in (first, second):
                self.assertEqual(record['files'][path.name]['after_sha256'],
                                 hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(all(not entry['changed'] for entry in api.apply(header, root)['files'].values()))
            header.write_text('class Modify {};\n')
            with self.assertRaisesRegex(ValueError, 'required accessor'):
                api.apply(header, root)

    def test_patched_lookup_compiles_and_preserves_missing_id_and_ttm_type_checks(self):
        compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('C++ compiler required for Modify API regression')
        source = '''#include <cassert>
#include <stdexcept>
#include <string>
using std::string;
#define FLERR 0
struct Compute {};
struct Fix { virtual ~Fix() = default; };
struct FixTTMDP : Fix {};
struct Modify {
  Compute *compute_value;
  Fix *fix_value;
  Compute *get_compute_by_id(const string &id) const {
    return id == "known" ? compute_value : nullptr;
  }
  Fix *get_fix_by_id(const string &id) const {
    return id == "known" ? fix_value : nullptr;
  }
};
struct Error {
  [[noreturn]] void all(int, const string &message) { throw std::runtime_error(message); }
};
'''
        for kind in ('compute', 'fix'):
            source += (kind.title() + '* find_' + kind
                       + '(Modify *modify, Error *error, const string &source_id) {\n'
                       + lookup(kind, 'source_id') + '}\n')
        # Original TTM lookup from DeePMD v3.2.0 pair_base.cpp.
        source += '''const FixTTMDP *find_ttm(Modify *modify, Error *error, const string &ttm_fix_id) {
  const FixTTMDP* ttm_fix = NULL;
  for (int ii = 0; ii < modify->nfix; ii++) {
    if (string(modify->fix[ii]->id) == ttm_fix_id) {
      ttm_fix = dynamic_cast<FixTTMDP*>(modify->fix[ii]);
    }
  }
  if (!ttm_fix) {
    error->all(FLERR, "fix ttm id is not found: " + ttm_fix_id);
  }
  return ttm_fix;
}
'''
        source += '''int main() {
  Compute compute;
  FixTTMDP ttm;
  Fix wrong_type;
  Modify modify{&compute, &ttm};
  Error error;
  assert(find_compute(&modify, &error, "known") == &compute);
  assert(find_fix(&modify, &error, "known") == &ttm);
  assert(find_ttm(&modify, &error, "known") == &ttm);
  int rejected = 0;
  try { find_compute(&modify, &error, "absent"); }
  catch (const std::runtime_error &e) { assert(string(e.what()) == "missing absent"); ++rejected; }
  try { find_fix(&modify, &error, "absent"); }
  catch (const std::runtime_error &e) { assert(string(e.what()) == "missing absent"); ++rejected; }
  try { find_ttm(&modify, &error, "absent"); }
  catch (const std::runtime_error &) { ++rejected; }
  modify.fix_value = &wrong_type;
  try { find_ttm(&modify, &error, "known"); }
  catch (const std::runtime_error &) { ++rejected; }
  assert(rejected == 4);
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'probe.cpp'
            path.write_text(source)
            command = [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', str(path), '-o', str(root / 'probe')]
            broken = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn('find_compute', broken.stderr)
            path.write_text(api.adapt(source)[0])
            fixed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            subprocess.run([str(root / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
