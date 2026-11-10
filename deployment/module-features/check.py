#!/usr/bin/env python3
"""Check all reviewed module descriptions using real Lmod, without loading them."""
import argparse
import json
import os
from pathlib import Path
import subprocess

PACKAGE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', action='store_true', help='inspect /opt rather than the preview')
    parser.add_argument('--output', type=Path, help='save full whatis/help output as JSON')
    args = parser.parse_args()
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    env = dict(os.environ)
    if not env.get('LMOD_CMD'):
        raise SystemExit('Run from a shell with the site Lmod initialization loaded.')
    # Do not inherit an interactive shell's loaded versions or preview paths.
    for key in list(env):
        if key.startswith(('_ModuleTable', '__LMOD_REF_COUNT_MODULEPATH', 'SAI_SIF_')) or key in ('LOADEDMODULES', '_LMFILES_'):
            env.pop(key)
    roots = [str(Path(manifest['root']) / name) for name in ('apps', 'devtools', 'compiler', 'mathlib', 'mpi')]
    if not args.installed:
        roots = [str(PACKAGE / 'modulefiles' / name) for name in ('apps', 'devtools')] + roots
    env['MODULEPATH'] = ':'.join(roots)
    results = []
    for row in manifest['modules']:
        module = row['path'].split('/', 1)[1].removesuffix('.lua')
        for action in ('whatis', 'help'):
            result = subprocess.run([env['LMOD_CMD'], 'bash', '--ignore_cache', action, module],
                                    env=env, text=True, capture_output=True, timeout=30)
            item = dict(module=module, mode=action, code=result.returncode,
                        output=result.stderr, features=result.stderr.count('Features:'))
            results.append(item)
            if item['code'] or not item['features']:
                print(f'FAIL {module} {action}: {result.stderr}')
    if args.output:
        args.output.write_text(json.dumps(results, indent=2) + '\n')
    failures = sum(bool(row['code'] or not row['features']) for row in results)
    print(f'{len(results)} Lmod checks; {failures} failures; '
          + ('installed modules' if args.installed else 'candidate preview'))
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()
