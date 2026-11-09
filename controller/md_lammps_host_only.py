#!/usr/bin/env python3
"""Apply LAMMPS PR #5308's tested CMake optimization inside the build checkout.

The upstream block is copied verbatim from Axel Kohlmeyer's commit below.
Already optimized or structurally unfamiliar sources retain their own build
rules. This is a local build adaptation, not a different upstream source SHA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex

UPSTREAM_COMMIT = 'f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7'
UPSTREAM_URL = 'https://github.com/lammps/lammps/pull/5308'
ANCHOR = ('get_property(LAMMPS_STYLE_SOURCES GLOBAL PROPERTY LAMMPS_STYLE_SOURCES)\n'
          'target_sources(lammps PRIVATE ${LAMMPS_STYLE_SOURCES})\n\n')

PATCH = r"""# With KOKKOS for CUDA all sources of LAMMPS are given to nvcc_wrapper, but nvcc is very slow and most
# of them do not use Kokkos, so these only need the host compiler.  Sources that use Kokkos include it
# through a header with kokkos in its name or one of the other headers below.  A source that gets it
# through any other header fails to compile, and then that header has to be added here.
if((PKG_KOKKOS) AND (Kokkos_ENABLE_CUDA) AND ((LAMMPS_CXX_COMPILER_NAME STREQUAL "nvcc_wrapper") OR (CMAKE_CXX_COMPILER_ID STREQUAL "GNU")))
  get_target_property(LAMMPS_ALL_SOURCES lammps SOURCES)
  foreach(LAMMPS_SRC ${LAMMPS_ALL_SOURCES})
    string(FIND "${LAMMPS_SRC}" "${LAMMPS_SOURCE_DIR}/" LAMMPS_SRC_POS)
    string(FIND "${LAMMPS_SRC}" "${LAMMPS_SOURCE_DIR}/KOKKOS/" KOKKOS_SRC_POS)
    if((LAMMPS_SRC_POS EQUAL 0) AND (NOT KOKKOS_SRC_POS EQUAL 0) AND (LAMMPS_SRC MATCHES "\\.cpp$"))
      file(STRINGS "${LAMMPS_SRC}" KOKKOS_INCLUDES REGEX
           "^[ \t]*#[ \t]*include[ \t]*[<\"]([A-Za-z_]*[Kk]okkos[A-Za-z_]*|random_external_state|reaxff_[a-z]+)\\.h")
      if(NOT KOKKOS_INCLUDES)
        set_source_files_properties("${LAMMPS_SRC}" PROPERTIES COMPILE_OPTIONS "--host-only")
      endif()
    endif()
  endforeach()
endif()

"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def integrate(text, wrapper):
    if PATCH in text:
        return text, 'already-present'
    # Upstream may revise its selection rules after this fixed backport. Do
    # not append a second implementation or overwrite the newer one.
    if re.search(r'^[^#\n]*--host-only', text, re.M):
        return text, 'upstream-managed'
    if '--host-only)' not in wrapper:
        return text, 'skipped-unsupported-wrapper'
    if text.count(ANCHOR) != 1:
        return text, 'skipped-unrecognized-cmake'
    return text.replace(ANCHOR, ANCHOR + PATCH, 1), 'applied'


def apply(source, receipt):
    source = Path(source).resolve()
    cmake = source / 'cmake/CMakeLists.txt'
    wrapper = (source / 'lib/kokkos/bin/nvcc_wrapper').read_text()
    before = cmake.read_text()
    after, status = integrate(before, wrapper)
    if after != before:
        cmake.write_text(after)
    result = dict(schema=1, upstream_commit=UPSTREAM_COMMIT, upstream_url=UPSTREAM_URL,
                  patch_sha256=digest(PATCH.encode()), source_root=str(source), status=status,
                  before_sha256=digest(before.encode()), after_sha256=digest(after.encode()))
    Path(receipt).write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def verify(database, receipt):
    receipt = Path(receipt)
    result = json.loads(receipt.read_text())
    source = Path(result['source_root'])
    if digest((source / 'cmake/CMakeLists.txt').read_bytes()) != result['after_sha256']:
        raise ValueError('LAMMPS CMake changed after host-only preparation')
    database = Path(database)
    rows = json.loads(database.read_text())
    selected = []
    for row in rows:
        arguments = row.get('arguments') or shlex.split(row['command'])
        if '--host-only' in arguments:
            file = Path(row['file'])
            if not file.is_absolute():
                file = Path(row['directory']) / file
            selected.append(file.resolve())
    if result['status'] in ('applied', 'already-present'):
        if not selected:
            raise ValueError('LAMMPS host-only optimization selected no compiler commands')
        if any(path.is_relative_to(source / 'src/KOKKOS') or path.suffix == '.cu'
               for path in selected):
            raise ValueError('Kokkos/CUDA source incorrectly selected for host-only compilation')
    result['compile_commands_sha256'] = digest(database.read_bytes())
    result['compiler_commands'] = len(rows)
    result['host_only_commands'] = len(selected)
    receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('apply', 'verify'))
    parser.add_argument('path', type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    result = {'apply': apply, 'verify': verify}[args.action](args.path, args.receipt)
    print('LAMMPS_HOST_ONLY ' + json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
