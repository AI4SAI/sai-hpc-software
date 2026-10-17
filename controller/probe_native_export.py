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

from export_native import (MANIFEST_PATH, FRAGMENT_PATH, canonical, checksum, export_image,
                           verify_export, write_manifests)
from release_contract import SOFTWARE, make_identity

COMMANDS = {"abacus": "abacus", "cp2k": "cp2k.psmp", "deepmd-kit": "dp",
            "lammps": "lmp", "gpumd": "gpumd"}


def prepare(directory):
    os.umask(0o022)
    directory = Path(directory).absolute()
    if directory.exists() or directory.parent.resolve(strict=True) != directory.parent:
        raise ValueError("probe requires a new path with an existing non-symlink parent")
    directory.mkdir(mode=0o755)
    root = directory / "fixture-root"
    entries = []
    for software, profile in SOFTWARE.items():
        for target in profile["targets"]:
            stack = {"deepmd-kit": "0" * 40, "lammps": "0" * 40} if software in ("deepmd-kit", "lammps") else None
            identity = make_identity(software, "development", "export-tool-fixture",
                                     "0" * 40, "NOT-SOFTWARE-EXPORT-PROBE", "0" * 64, target,
                                     stack_sources=stack)
            prefix = root / identity["install_prefix"].lstrip("/")
            (prefix / "bin").mkdir(parents=True)
            command = prefix / "bin" / COMMANDS[software]
            command.write_text('#!/bin/sh\necho "NOT SOFTWARE: export-tool fixture only" >&2\nexit 125\n')
            command.chmod(0o755)
            (prefix / "fixture-link").symlink_to("bin/" + COMMANDS[software])
            entries.append({"identity": identity, "commands": {COMMANDS[software]: "bin/" + COMMANDS[software]},
                            "external_roots": [], "runtime": {"modules": [], "prepend": {}, "set": {}}})
    write_manifests(entries, root)
    (root / "DO-NOT-EXPORT").write_text("outside declared software prefixes\n")
    squashfs = directory / "fixture.squashfs"
    subprocess.run(["mksquashfs", str(root), str(squashfs), "-noappend", "-no-progress",
                    "-processors", "1"], check=True)
    print(canonical({"fixture": str(squashfs), "sha256": checksum(squashfs),
                     "scientific_artifact": False}), end="")


def verify(image, destination, prefixes=None):
    receipt = export_image(image, checksum(image), destination, prefixes=prefixes)
    destination = Path(destination)
    if (destination / "rootfs").exists() or (destination / "DO-NOT-EXPORT").exists():
        raise ValueError("rootfs shell or unselected image file was exported")
    if not prefixes and len(receipt["expected_install_prefixes"]) != sum(len(p["targets"]) for p in SOFTWARE.values()):
        raise ValueError("wrong number of selected fixture installations")
    identities = []
    for installation in receipt["installations"]:
        prefix = installation["expected_install_prefix"]
        directory = destination / installation["path"]
        manifest = directory / MANIFEST_PATH
        if checksum(manifest) != installation["manifest_sha256"]:
            raise ValueError("manifest receipt checksum differs")
        local = json.loads(manifest.read_text())
        identity = local["entry"]["identity"]
        identities.append(identity)
        command = "bin/" + COMMANDS[identity["software"]]
        if (directory / "fixture-link").readlink() != Path(command):
            raise ValueError("fixture symlink changed")
        if "NOT SOFTWARE" not in (directory / command).read_text():
            raise ValueError("fixture bytes changed")
        if identity["install_prefix"] != prefix or identity["source_version"] != "NOT-SOFTWARE-EXPORT-PROBE":
            raise ValueError("probe identity changed or was mistaken for real software")
        for relative in (FRAGMENT_PATH,
                         f'modulefiles/{identity["software"]}/development/{identity["build_id"]}'):
            module = directory / relative
            row = next(row for row in local["files"] if row["path"] == prefix.lstrip("/") + "/" + relative)
            if str(destination) in module.read_text() or checksum(module) != row["sha256"]:
                raise ValueError("embedded module paths or inventory checksums differ")
    verified = verify_export(destination, receipt, checksum(image), identities)
    result = {"status": "PASS", **verified, "scientific_artifact": False, "container_executed": False,
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
    check.add_argument("--prefix", action="append", help="optional canonical fixture prefix for a single-folder check")
    args = parser.parse_args()
    if args.action == "prepare-local":
        prepare(args.directory)
    else:
        verify(args.image, args.destination, prefixes=args.prefix)
