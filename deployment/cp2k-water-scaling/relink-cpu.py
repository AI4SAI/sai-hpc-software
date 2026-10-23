#!/usr/bin/env python3
"""Relink a control and a one-object repair from the same read-only CPU build."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def main(run, original):
    run, original = Path(run), Path(original)
    request = json.loads((run/'request.json').read_text())
    assert all(digest(run/p) == value for p,value in request['inputs'].items()), 'Frozen control changed'
    assert request['original'] == str(original)
    def cpu_target(path):
        return {k: v.strip() for k,v in re.findall(r'^(Model name|CPU family|Model|Flags):\s*(.*)$', path.read_text(), re.M)}
    original_target = cpu_target(original/'results/lscpu.txt')
    current_target = cpu_target(run/'results/lscpu.txt')
    assert original_target and current_target == original_target, 'The -march=native CPU target differs from the original build'
    build = original/'work/cp2k-build/src'
    source = original/'work/source/src/fm/cp_fm_diag_utils.F'
    spec = importlib.util.spec_from_file_location('redistribution_fix', Path(__file__).with_name('fix-redistribution.py'))
    fix = importlib.util.module_from_spec(spec); spec.loader.exec_module(fix)
    fixed_source = run/'work/cp_fm_diag_utils.F'
    record = fix.repair(source, fixed_source)
    save(run/'results/source-fix.json', record)
    (run/'results/source.patch').write_text(record['diff'])
    original_object = build/'CMakeFiles/cp2k.dir/fm/cp_fm_diag_utils.F.o'
    generated = run/'work/generated/cp_fm_diag_utils.F'
    generated.parent.mkdir()
    subprocess.run(['/usr/bin/python3.12', str(original/'work/source/tools/build_utils/fypp'),
                    '--line-numbering', '--line-marker-format=gfortran5', str(fixed_source), str(generated)], check=True)
    # Retain the original diagnostic source name; only the two functional hunks differ.
    generated.write_text(generated.read_text().replace(str(fixed_source), str(source)))
    flags = dict(re.findall(r'^(Fortran_\w+) = (.*)$', (build/'CMakeFiles/cp2k.dir/flags.make').read_text(), re.M))
    module_dir = run/'work/mod_files'; module_dir.mkdir()
    fixed_object = run/'work/cp_fm_diag_utils.F.o'
    compile_argv = ['/opt/devtools/gcc/14.3.0/bin/gfortran', *shlex.split(flags['Fortran_DEFINES']),
        '-D__SHORT_FILE__="fm/cp_fm_diag_utils.F"', *shlex.split(flags['Fortran_INCLUDES']),
        *[('-J'+str(module_dir)) if x == '-Jmod_files' else x for x in shlex.split(flags['Fortran_FLAGS'])],
        '-c', str(generated), '-o', str(fixed_object)]
    save(run/'results/compile-command.json', dict(argv=compile_argv, cwd=str(build)))
    subprocess.run(compile_argv, cwd=build, check=True)
    assert digest(module_dir/'cp_fm_diag_utils.mod') == digest(build/'mod_files/cp_fm_diag_utils.mod'), 'Public Fortran interface changed'
    link = shlex.split((build/'CMakeFiles/cp2k.dir/link.txt').read_text())
    object_name = str(original_object.relative_to(build))
    assert link.count(object_name) == 1
    objects = {str((build/p).resolve()): digest(build/p) for p in link if p.endswith('.o')}
    assert len(objects) > 1000
    save(run/'results/reused-objects.json', objects)
    libraries = {p: digest(p) for p in link if p.startswith('/') and Path(p).is_file() and p != link[0]}
    save(run/'results/link-libraries.json', libraries)
    for variant, prefix in (('before', run/'before'), ('after', run/'install')):
        for folder in ('bin', 'lib64', 'share/sai'):
            (prefix/folder).mkdir(parents=True, exist_ok=True)
        shutil.copy2(original/'install/bin/cp2k.psmp', prefix/'bin/cp2k.psmp')
        (prefix/'dependencies').symlink_to(original/'install/dependencies', target_is_directory=True)
        (prefix/'share/cp2k').symlink_to(original/'install/share/cp2k', target_is_directory=True)
        for name in ('CMakeCache.txt', 'source-sha', 'DBCSR-CMakeCache.txt', 'elpa-gpu-setup-fix.json'):
            shutil.copy2(original/'install/share/sai'/name, prefix/'share/sai'/name)
        output = prefix/'lib64/libcp2k.so.2026.2'
        argv = list(link)
        argv[argv.index('-o')+1] = str(output)
        argv = [('-Wl,--dependency-file='+str(run/'work'/('link-'+variant+'.d')))
                if x.startswith('-Wl,--dependency-file=') else x for x in argv]
        if variant == 'after':
            argv[argv.index(object_name)] = str(fixed_object)
        save(run/'results'/('link-'+variant+'.json'), dict(argv=argv, cwd=str(build)))
        subprocess.run(argv, cwd=build, check=True)
        if variant == 'before':
            assert digest(output) == digest(build/'libcp2k.so.2026.2'), 'Control relink is not byte-identical to original build'
        # Use the exact install RPATH from the original CMake install, for both variants.
        dynamic = subprocess.check_output(['readelf', '-d', str(output)], text=True)
        old_rpath = re.search(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic)[1]
        dynamic = subprocess.check_output(['readelf', '-d', str(original/'install/lib64/libcp2k.so.2026.2')], text=True)
        new_rpath = re.search(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic)[1]
        cmake = run/'work'/('rpath-'+variant+'.cmake')
        cmake.write_text(f'file(RPATH_CHANGE FILE "{output}" OLD_RPATH [==[{old_rpath}]==] NEW_RPATH [==[{new_rpath}]==])\n')
        subprocess.run(['cmake', '-P', str(cmake)], check=True)
        (prefix/'lib64/libcp2k.so').symlink_to(output.name)
        if variant == 'before':
            assert digest(output) == digest(original/'install/lib64/libcp2k.so.2026.2'), 'Installed control differs'
        env = dict(os.environ, CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   LD_LIBRARY_PATH=':'.join(str(prefix/p) for p in ('lib64', 'dependencies/dbcsr/lib64', 'dependencies/libxs/lib'))+':'+os.environ.get('LD_LIBRARY_PATH',''))
        for name, command in (('version', [str(prefix/'bin/cp2k.psmp'), '--version']), ('ldd', ['ldd', str(prefix/'bin/cp2k.psmp')])):
            result = subprocess.check_output(command, env=env, text=True, stderr=subprocess.STDOUT)
            (prefix/'share/sai'/f'{name}.txt').write_text(result)
            if name == 'ldd':
                assert 'not found' not in result and str(output) in result
                assert not re.search(r'lib(?:cusolver|cublas|cufft|elpa|spla|cosma)', result)
        save(prefix/'share/sai/redistribution-repair.json', dict(record, variant=variant,
             original_build=str(original), executable_sha256=digest(prefix/'bin/cp2k.psmp'), library_sha256=digest(output)))
    assert all(digest(p) == value for p,value in objects.items()), 'Original objects changed'
    assert all(digest(p) == value for p,value in libraries.items()), 'Link libraries changed'
    save(run/'results/relink-proof.json', dict(original=str(original), changed_object=object_name,
         original_object_sha256=digest(original_object), fixed_object_sha256=digest(fixed_object),
         reused_object_count=len(objects)-1, control_byte_identical=True, public_module_byte_identical=True,
         cpu_target=current_target,
         compiler=subprocess.check_output([compile_argv[0], '--version'], text=True).splitlines()[0]))
    subprocess.run(['mpicc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared',
                    str(Path(__file__).with_name('trace-scalapack.c')), '-ldl', '-o', str(run/'install/lib64/trace-scalapack.so')], check=True)
    (run/'results/build-complete').touch()
    print('CPU_ONE_OBJECT_REPAIR_COMPLETE_MPI_ACCEPTANCE_PENDING', flush=True)


if __name__ == '__main__':
    main(*sys.argv[1:])
