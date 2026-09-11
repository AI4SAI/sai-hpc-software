#!/usr/bin/env python3
"""Inventory and export installed SIF prefixes without executing container code.

An export is an integrity-checked staging bundle, NOT scientific acceptance or
proof of relocation. Its modulefiles always name the original /opt prefixes.
Only regular-file bytes are read with unsquashfs -cat; directory creation and
symlinks are handled here from a validated, embedded declarative inventory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import tempfile

from native_module import validate_native_entry, render_native_fragment, render_native_selector
from source_cache import checksum

MANIFEST_PATH = "opt/sai-delivery/manifest.json"
FRAGMENT_PATH = "share/sai/native-module.tcl"
MAX_MANIFEST_BYTES = 64 * 1024**2


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            value.startswith("/") or value != str(PurePosixPath(value)) or
            any(part in (".", "..") for part in value.split("/")) or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("unsafe inventory path")
    return value


def beneath(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def compatible_prefixes(identity, identities):
    return [peer["install_prefix"] for peer in identities
            if peer["partition"] == identity["partition"] and
            (not identity["stack_digest"] or peer["stack_digest"] == identity["stack_digest"])]


def resolve_inventory_path(path, indexed, prefixes, external_roots):
    """Resolve links component-wise, with .. applied AFTER link expansion."""
    pending, resolved, links = path.split("/"), [], 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if resolved:
                resolved.pop()
            continue
        resolved.append(part)
        candidate = "/" + "/".join(resolved)
        record = indexed.get(candidate.lstrip("/"))
        if record and record["kind"] == "symlink":
            links += 1
            if links > 40:
                raise ValueError("cyclic or excessively deep inventory symlink")
            resolved.pop()
            target = record["target"]
            if target.startswith("/"):
                resolved = []
            pending = target.split("/") + pending
        elif record and record["kind"] != "directory" and pending:
            raise ValueError("inventory path traverses a regular file")
        elif record is None and beneath(candidate, prefixes):
            raise ValueError("inventory path traverses a missing internal component")
    result = "/" + "/".join(resolved)
    if beneath(result, prefixes):
        if result.lstrip("/") not in indexed:
            raise ValueError("dangling internal inventory symlink")
    elif not beneath(result, external_roots):
        raise ValueError("resolved path escapes this partition and declared external roots")
    return result


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "entries", "files"} or
            type(manifest["schema"]) is not int or manifest["schema"] != 1 or
            not isinstance(manifest["entries"], list) or not manifest["entries"] or
            not isinstance(manifest["files"], list)):
        raise ValueError("invalid delivery inventory schema")
    from release_contract import validate_identity
    identities = [validate_identity(entry["identity"]) for entry in manifest["entries"]]
    prefixes = [identity["install_prefix"] for identity in identities]
    if len(set(prefixes)) != len(prefixes):
        raise ValueError("duplicate delivery prefix")
    for identity in identities:
        if identity["stack_digest"]:
            companion = "lammps" if identity["software"] == "deepmd-kit" else "deepmd-kit"
            matches = [peer for peer in identities
                       if peer["software"] == companion and peer["partition"] == identity["partition"] and
                       peer["stack_digest"] == identity["stack_digest"] and
                       peer["source_sha"] == identity["stack_sources"][companion]]
            if len(matches) != 1:
                raise ValueError("paired delivery is missing its exactly locked companion")
    entries = [validate_native_entry(entry, allowed_prefixes=compatible_prefixes(identity, identities))
               for entry, identity in zip(manifest["entries"], identities)]
    roots = [prefix.lstrip("/") for prefix in prefixes]
    reserved = {root + "/" + FRAGMENT_PATH for root in roots}
    indexed = {}
    for record in manifest["files"]:
        if not isinstance(record, dict):
            raise ValueError("invalid inventory record")
        path = relative_path(record.get("path"))
        if path in indexed or path in reserved or not beneath(path, roots):
            raise ValueError("duplicate, reserved or out-of-prefix inventory path")
        kind, mode = record.get("kind"), record.get("mode")
        fields = {"path", "kind", "mode"}
        if type(mode) is not int or mode < 0 or mode > 0o777 or mode & 0o022:
            raise ValueError("unsafe installed-tree mode")
        if kind == "file":
            fields |= {"size", "sha256"}
            if (type(record.get("size")) is not int or record["size"] < 0 or
                    not isinstance(record.get("sha256"), str) or
                    not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) or
                    mode & 0o444 != 0o444 or (mode & 0o111 and mode & 0o111 != 0o111)):
                raise ValueError("unreadable or malformed installed file")
        elif kind == "directory":
            if mode & 0o555 != 0o555:
                raise ValueError("installed directory is not world readable/traversable")
        elif kind == "symlink":
            # Link permissions have no access meaning. Use a normalized 0755 in
            # the portable inventory, not the source filesystem's usual 0777.
            fields |= {"target"}
            target = record.get("target")
            if (not isinstance(target, str) or not target or len(target) > 4096 or
                    any(ord(c) < 32 or ord(c) == 127 for c in target)):
                raise ValueError("invalid symlink target")
        else:
            raise ValueError("special files are forbidden in native deliveries")
        if set(record) != fields:
            raise ValueError("unknown inventory fields")
        indexed[path] = dict(record)
    for root in roots:
        if indexed.get(root, {}).get("kind") != "directory":
            raise ValueError("missing installed prefix directory")
    for path in indexed:
        parent = posixpath.dirname(path)
        while beneath(parent, roots):
            if indexed.get(parent, {}).get("kind") != "directory":
                raise ValueError("missing directory or symlink ancestor")
            parent = posixpath.dirname(parent)
    for entry in entries:
        prefix = entry["identity"]["install_prefix"].lstrip("/")
        local_prefixes = compatible_prefixes(entry["identity"], identities)
        for path, row in indexed.items():
            if beneath(path, [prefix]) and row["kind"] == "symlink":
                resolve_inventory_path("/" + path, indexed, local_prefixes, entry["external_roots"])
        for directory in (prefix + "/share", prefix + "/share/sai"):
            if directory in indexed and indexed[directory]["kind"] != "directory":
                raise ValueError("generated module requires regular share/sai directories")
        for command in entry["commands"].values():
            executable = indexed.get(prefix + "/" + command, {})
            if executable.get("kind") != "file" or executable["mode"] & 0o111 != 0o111:
                raise ValueError("declared command must be a delivered executable regular file")
    return {"schema": 1, "entries": entries,
            "files": [indexed[path] for path in sorted(indexed)]}


def inventory(entries, root=Path("/")):
    """Run inside the build container after install; never source runtime shell."""
    records = []
    root = Path(root).resolve(strict=True)
    for entry in entries:
        from release_contract import validate_identity
        prefix = validate_identity(entry["identity"])["install_prefix"].lstrip("/")
        base = root / prefix
        if base.is_symlink() or base.resolve() != base:
            raise ValueError("installed prefix has a symlink ancestor")
        pending = [base]
        while pending:
            path = pending.pop()
            info = path.lstat()
            record = {"path": path.relative_to(root).as_posix(), "mode": stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                record.update(kind="symlink", target=os.readlink(path), mode=0o755)
            elif stat.S_ISDIR(info.st_mode):
                record.update(kind="directory")
                pending.extend(sorted(path.iterdir(), reverse=True))
            elif stat.S_ISREG(info.st_mode):
                record.update(kind="file", size=info.st_size, sha256=checksum(path))
            else:
                raise ValueError("installed tree contains a special file")
            records.append(record)
    return validate_manifest({"schema": 1, "entries": entries, "files": records})


class SquashfsReader:
    def __init__(self, image, offset):
        self.image = Path(image)
        self.offset = offset

    @classmethod
    def from_sif(cls, image):
        listing = subprocess.check_output(["apptainer", "sif", "list", str(image)], text=True)
        partitions = re.findall(r"^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-(\d+)\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$",
                                listing, re.MULTILINE)
        if len(partitions) != 1:
            raise ValueError("SIF must contain exactly one primary Squashfs system partition")
        start, end = map(int, partitions[0])
        if not 0 <= start < end <= Path(image).stat().st_size:
            raise ValueError("invalid SIF partition boundaries")
        return cls(image, start)

    def command(self, paths):
        return ["unsquashfs", "-no-progress", "-no-wildcards", "-o", str(self.offset),
                "-cat", str(self.image), *paths]

    def manifest(self):
        with subprocess.Popen(self.command([MANIFEST_PATH]), stdout=subprocess.PIPE) as process:
            data = process.stdout.read(MAX_MANIFEST_BYTES + 1)
            if len(data) > MAX_MANIFEST_BYTES:
                process.kill()
                raise ValueError("embedded delivery manifest exceeds size limit")
            if process.wait() != 0:
                raise ValueError("cannot read embedded delivery manifest")
        return validate_manifest(json.loads(data))

    def validate_metadata(self, manifest, *, selected_link=None):
        """Check real file boundaries/types before concatenated -cat reads.

        Without this check, a forged size could move bytes from one file to
        another while preserving the overall concatenated stream checksum.
        Match complete expected listing tails, not whitespace-split filenames.
        """
        roots = ([selected_link["path"]] if selected_link else
                 [entry["identity"]["install_prefix"].lstrip("/") for entry in manifest["entries"]])
        records = [selected_link] if selected_link else manifest["files"]
        expected = {}
        for row in records:
            tail = "/image/" + row["path"]
            if row["kind"] == "symlink":
                tail += " -> " + row["target"]
            if tail in expected:
                raise ValueError("ambiguous Squashfs inventory listing")
            expected[tail] = row
        ancestors = {"/image"}
        for root in roots:
            path = posixpath.dirname(root)
            while path:
                ancestors.add("/image/" + path)
                path = posixpath.dirname(path)
        listing = subprocess.check_output(
            ["unsquashfs", "-lln", "-UTC", "-full-precision", "-d", "/image", "-no-progress",
             "-no-wildcards", "-o", str(self.offset), str(self.image), *roots],
            text=True, env=dict(os.environ, LC_ALL="C"))
        seen = set()
        for line in listing.splitlines():
            match = re.fullmatch(r"([dl-][rwxstST-]{9})\s+\d+/\d+\s+(\d+)\s+\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d (.+)", line)
            if not match:
                raise ValueError("unsupported or unsafe Squashfs listing record")
            mode, size, tail = match.groups()
            if tail in ancestors:
                if mode[0] != "d":
                    raise ValueError("SIF install prefix has a non-directory ancestor")
                continue
            row = expected.get(tail)
            if row is None or tail in seen:
                raise ValueError("SIF files differ from the declared installed tree")
            seen.add(tail)
            file_type = {"file": stat.S_IFREG, "directory": stat.S_IFDIR, "symlink": stat.S_IFLNK}[row["kind"]]
            expected_mode = stat.filemode(file_type | (0o777 if row["kind"] == "symlink" else row["mode"]))
            expected_size = (row["size"] if row["kind"] == "file" else
                             len(row["target"].encode()) if row["kind"] == "symlink" else None)
            if mode != expected_mode or (expected_size is not None and int(size) != expected_size):
                raise ValueError("SIF file mode, type or size differs from inventory")
        if seen != set(expected):
            raise ValueError("SIF is missing inventoried installed files")
        if selected_link is None:
            # Human-readable listings use ' -> ' for links. A link's name can
            # contain that delimiter too, so independently select each literal
            # link path to prove its existence (no globs or follow-symlinks).
            # This also prevents newline-bearing unseen names from fabricating
            # listing lines for links that do not actually exist in the SIF.
            for row in records:
                if row["kind"] == "symlink":
                    self.validate_metadata(manifest, selected_link=row)

    def copy_files(self, records, root):
        # Concatenate known-size regular files in bounded batches: efficient
        # for Python environments without asking unsquashfs to create any paths.
        for start in range(0, len(records), 64):
            batch = records[start:start + 64]
            with subprocess.Popen(self.command([row["path"] for row in batch]),
                                  stdout=subprocess.PIPE) as process:
                try:
                    for row in batch:
                        digest = hashlib.sha256()
                        remaining = row["size"]
                        path = root / row["path"]
                        with path.open("xb") as output:
                            while remaining:
                                data = process.stdout.read(min(remaining, 1024**2))
                                if not data:
                                    raise ValueError("short file stream from image")
                                output.write(data)
                                digest.update(data)
                                remaining -= len(data)
                        if digest.hexdigest() != row["sha256"]:
                            raise ValueError("exported file checksum differs from inventory")
                        path.chmod(row["mode"])
                    if process.stdout.read(1) or process.wait() != 0:
                        raise ValueError("unexpected bytes or failed Squashfs read")
                except BaseException:
                    process.kill()
                    raise


def export_image(image, expected_sha256, destination, *, reader_factory=SquashfsReader.from_sif):
    image = Path(image).resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        raise ValueError("expected image SHA256 is required")
    if checksum(image) != expected_sha256:
        raise ValueError("image checksum does not match the expected artifact")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("export destination must not already exist")
    if destination.parent.resolve(strict=True) != destination.parent:
        raise ValueError("destination parent must exist without symlink ancestors")
    reader = reader_factory(image)
    manifest = validate_manifest(reader.manifest())
    reader.validate_metadata(manifest)
    prefixes = [entry["identity"]["install_prefix"] for entry in manifest["entries"]]
    # TemporaryDirectory only removes the exact newly-created private staging
    # directory on failure. No user destination or existing software is removed.
    with tempfile.TemporaryDirectory(prefix=".sai-native-export-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "bundle"
        root = stage / "rootfs"
        root.mkdir(parents=True, mode=0o755)
        for row in manifest["files"]:
            if row["kind"] == "directory":
                (root / row["path"]).mkdir(parents=True, exist_ok=True, mode=0o755)
        reader.copy_files([row for row in manifest["files"] if row["kind"] == "file"], root)
        # Links are created last, never traversed during extraction or hashing.
        for row in manifest["files"]:
            if row["kind"] == "symlink":
                (root / row["path"]).symlink_to(row["target"])
        generated = []
        for entry in manifest["entries"]:
            identity = entry["identity"]
            fragment = root / identity["install_prefix"].lstrip("/") / FRAGMENT_PATH
            # Missing share/sai parents may be added, but never through links.
            current = fragment.parent
            while current != root:
                if current.is_symlink():
                    raise ValueError("module fragment parent is a symlink")
                current = current.parent
            fragment.parent.mkdir(parents=True, exist_ok=True)
            fragment.write_text(render_native_fragment(entry, allowed_prefixes=prefixes))
            fragment.chmod(0o444)
            selector = stage / "modulefiles" / identity["software"] / identity["track"] / identity["build_id"]
            selector.parent.mkdir(parents=True, exist_ok=True)
            content = render_native_selector(identity)
            if selector.exists():
                if selector.read_text() != content:
                    raise ValueError("partition selectors disagree")
            else:
                selector.write_text(content)
                selector.chmod(0o444)
            for path in (fragment, selector):
                row = {"path": path.relative_to(stage).as_posix(), "sha256": checksum(path)}
                if row not in generated:
                    generated.append(row)
        # Do not let a restrictive caller umask create inaccessible ancestors
        # outside the inventoried prefixes (rootfs/opt, modulefiles, share/sai).
        for current, directories, _ in os.walk(stage, followlinks=False):
            Path(current).chmod(0o755)
            directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
        for row in reversed(manifest["files"]):
            if row["kind"] == "directory":
                (root / row["path"]).chmod(row["mode"])
        receipt = {"schema": 1, "image_sha256": expected_sha256,
                   "inventory_sha256": hashlib.sha256(canonical(manifest).encode()).hexdigest(),
                   "expected_install_prefixes": prefixes, "generated_files": generated,
                   "validation": "integrity-checked; native science and performance acceptance still required",
                   "relocation": "staging only; install at expected /opt prefixes or separately validate relocation"}
        (stage / "inventory.json").write_text(canonical(manifest))
        (stage / "export.json").write_text(canonical(receipt))
        (stage / "README.txt").write_text(
            "This is a staging bundle, not a privileged installer.\n"
            "Copy each tree beneath rootfs to its matching absolute /opt path.\n"
            "Expected prefixes:\n" + "".join("  " + path + "\n" for path in prefixes) +
            "Keep modulefiles at any shared location; module use that location.\n"
            "Load software/track/build_id inside a matching Slurm allocation.\n"
            "Do not replace the /opt paths with this staging directory.\n"
            "External site dependency modules must remain available.\n"
            "This export does not certify native scientific results or speed.\n")
        if checksum(image) != expected_sha256:
            raise ValueError("image changed during export")
        # Reserve destination atomically; never replace an existing user path.
        destination.mkdir(mode=0o700)
        try:
            for child in stage.iterdir():
                child.rename(destination / child.name)
            destination.chmod(0o755)
        except BaseException:
            # Leave any already moved files recoverable; never recursively
            # remove a destination once exposed outside our private staging.
            raise RuntimeError(f"incomplete export preserved at {destination}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="inside build container: inventory installed prefixes")
    inv.add_argument("--entry", type=Path, action="append", required=True)
    inv.add_argument("--output", type=Path, default=Path("/" + MANIFEST_PATH))
    exp = sub.add_parser("export", help="stage a SIF's declared installed trees without executing it")
    exp.add_argument("image", type=Path)
    exp.add_argument("--image-sha256", required=True)
    exp.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory([json.loads(path.read_text()) for path in args.entry])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            output.write(canonical(result))
        args.output.chmod(0o444)
    else:
        print(canonical(export_image(args.image, args.image_sha256, args.destination)), end="")


if __name__ == "__main__":
    main()
