#!/usr/bin/env python3
"""Decode CMake response-file quoting before Kokkos nvcc_wrapper sees argv.

nvcc_wrapper expands @*.rsp with shell word splitting, which leaves literal
quotes around e.g. CG-DNA object paths. Those tokens no longer match *.o and
are forwarded through -Xcompiler, breaking NVCC's device-link stub compile.
Use CMAKE_CXX_LINKER_LAUNCHER so compilation itself is unchanged and the long
object list is passed as argv, never as one oversized shell command string.
"""
import os
from pathlib import Path
import shlex
import sys


def expand_response_files(arguments, ancestors=()):
    expanded = []
    for argument in arguments:
        if argument.startswith('@') and argument.endswith('.rsp'):
            path = Path(argument[1:]).resolve(strict=True)
            if path in ancestors or len(ancestors) >= 16:
                raise ValueError('cyclic or excessively nested linker response file')
            expanded.extend(expand_response_files(shlex.split(path.read_text()), (*ancestors, path)))
        else:
            expanded.append(argument)
    return expanded


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if not arguments:
        raise ValueError('linker compiler and arguments required')
    command = [arguments[0], *expand_response_files(arguments[1:])]
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
