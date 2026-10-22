#!/usr/bin/env python3
"""Remove the site MPI libtool HCOLL alias from newly installed private ELPA."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from cp2k_feature_contract import is_hcoll_build_path


def runpaths(library):
    dynamic = subprocess.check_output(["readelf", "-d", str(library)], text=True)
    return [{"tag": tag, "value": value} for tag, value in
            re.findall(r"\((RPATH|RUNPATH)\).*?\[(.*?)\]", dynamic)]


def remove_build_alias(library):
    library = Path(library).resolve(strict=True)
    before = runpaths(library)
    if len(before) > 1:
        raise ValueError("ambiguous private ELPA loader paths")
    original_sha256 = hashlib.sha256(library.read_bytes()).hexdigest()
    expected = before
    if before:
        paths = before[0]["value"].split(":")
        retained = [path for path in paths if not is_hcoll_build_path(path)]
        if retained != paths:
            # Removal only shortens the existing string: chrpath preserves
            # ELF layout and all other search entries, including $ORIGIN.
            if retained:
                expected = [dict(before[0], value=":".join(retained))]
                argv = ["chrpath", "-r", expected[0]["value"], str(library)]
            else:
                expected = []
                argv = ["chrpath", "-d", str(library)]
            subprocess.run(argv, check=True, capture_output=True, text=True)
    after = runpaths(library)
    if after != expected:
        raise ValueError("private ELPA loader paths differ after removing build alias")
    return dict(library=str(library), before=before, after=after,
                changed=before != after, original_sha256=original_sha256,
                installed_sha256=hashlib.sha256(library.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=Path)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    receipt = remove_build_alias(args.library)
    with args.receipt.open("x") as stream:
        stream.write(json.dumps(receipt, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
