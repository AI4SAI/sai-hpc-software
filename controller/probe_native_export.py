#!/usr/bin/env python3
"""Tiny export-tool fixture, explicitly NOT an ABACUS/scientific build.

Prepare the compressed fixture locally, then transfer just its Squashfs plus
these trusted controller helpers. On SAI, sif new/add packages it without a
host source checkout or container execution. Nothing is installed into /opt.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from export_native import (MANIFEST_PATH, canonical, checksum, export_image,
                           inventory)
from release_contract import make_identity


def prepare(directory):
    os.umask(0o022)
    directory = Path(directory).absolute()
    if directory.exists() or directory.parent.resolve(strict=True) != directory.parent:
        raise ValueError("probe requires a new path with an existing non-symlink parent")
    directory.mkdir(mode=0o755)
    root = directory / "fixture-root"
    entries = []
    for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
        identity = make_identity("abacus", "development", "export-tool-fixture",
                                 "0" * 40, "NOT-SOFTWARE-EXPORT-PROBE", "0" * 64, target)
        prefix = root / identity["install_prefix"].lstrip("/")
        (prefix / "bin").mkdir(parents=True)
        command = prefix / "bin/abacus"
        command.write_text('#!/bin/sh\necho "NOT ABACUS: export-tool fixture only" >&2\nexit 125\n')
        command.chmod(0o755)
        (prefix / "fixture-link").symlink_to("bin/abacus")
        entries.append({"identity": identity, "commands": {"abacus": "bin/abacus"},
                        "external_roots": [], "runtime": {"modules": [], "prepend": {}, "set": {}}})
    metadata = root / MANIFEST_PATH
    metadata.parent.mkdir(parents=True)
    metadata.write_text(canonical(inventory(entries, root)))
    (root / "DO-NOT-EXPORT").write_text("outside declared software prefixes\n")
    squashfs = directory / "fixture.squashfs"
    subprocess.run(["mksquashfs", str(root), str(squashfs), "-noappend", "-no-progress",
                    "-processors", "1"], check=True)
    print(canonical({"fixture": str(squashfs), "sha256": checksum(squashfs),
                     "scientific_artifact": False}), end="")


def verify(image, destination):
    receipt = export_image(image, checksum(image), destination)
    destination = Path(destination)
    if (destination / "rootfs/DO-NOT-EXPORT").exists():
        raise ValueError("unselected image file was exported")
    if len(receipt["expected_install_prefixes"]) != 4 or len(receipt["generated_files"]) != 5:
        raise ValueError("four partitions must share exactly one selector")
    for prefix in receipt["expected_install_prefixes"]:
        directory = destination / "rootfs" / prefix.lstrip("/")
        if (directory / "fixture-link").readlink() != Path("bin/abacus"):
            raise ValueError("fixture symlink changed")
        if "NOT ABACUS" not in (directory / "bin/abacus").read_text():
            raise ValueError("fixture bytes changed")
    for record in receipt["generated_files"]:
        module = destination / record["path"]
        if str(destination) in module.read_text() or checksum(module) != record["sha256"]:
            raise ValueError("module paths or receipt checksums differ")
    result = {"status": "PASS", "scientific_artifact": False, "container_executed": False,
              "system_opt_modified": False, "export_receipt": receipt}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    stage = commands.add_parser("prepare-local")
    stage.add_argument("directory", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("image", type=Path)
    check.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.action == "prepare-local":
        prepare(args.directory)
    else:
        verify(args.image, args.destination)
