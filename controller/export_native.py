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

MANIFEST_PATH = "share/sai/manifest.json"
FRAGMENT_PATH = "share/sai/native-module.tcl"
EXPORT_PATH = "share/sai/export.json"
README_PATH = "share/sai/README-export.txt"
RESERVED_PATHS = (MANIFEST_PATH, EXPORT_PATH, README_PATH)
MAX_MANIFEST_BYTES = 64 * 1024**2


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def parse_manifest(data):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate manifest JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique_object)


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
            (not identity["stack_digest"] or
             (peer["stack_digest"] == identity["stack_digest"] and
              peer["recipe_sha256"] == identity["recipe_sha256"]))]


def companion_for(identity, identities):
    companion = "lammps" if identity["software"] == "deepmd-kit" else "deepmd-kit"
    matches = [peer for peer in identities
               if peer["software"] == companion and peer["partition"] == identity["partition"] and
               peer["stack_digest"] == identity["stack_digest"] and
               peer["recipe_sha256"] == identity["recipe_sha256"] and
               peer["source_sha"] == identity["stack_sources"][companion]]
    if len(matches) != 1:
        raise ValueError("paired delivery is missing its exactly locked companion")
    return matches[0]


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
            companion_for(identity, identities)
    entries = [validate_native_entry(entry, allowed_prefixes=compatible_prefixes(identity, identities))
               for entry, identity in zip(manifest["entries"], identities)]
    roots = [prefix.lstrip("/") for prefix in prefixes]
    reserved = {root + "/" + path for root in roots for path in RESERVED_PATHS}
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
            if path == base / MANIFEST_PATH:
                # A file cannot contain its own checksum. The SIF digest and
                # export receipt bind these exact manifest bytes instead.
                if path.is_symlink() or not path.is_file():
                    raise ValueError("installed manifest must be a regular file")
                continue
            if path in (base / EXPORT_PATH, base / README_PATH):
                raise ValueError("export receipt paths are reserved, not build inputs")
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


def write_manifests(entries, root=Path("/")):
    """Package native modules, then write one schema 2 manifest per prefix.

    All writes stay inside installed prefixes. The returned aggregate schema 1
    is an internal validation API, not an extra file in the container rootfs.
    Existing identical manifests are allowed; conflicting files are preserved.
    """
    from native_module import package_native_modules
    from release_contract import validate_identity
    root = Path(root).resolve(strict=True)
    identities = [validate_identity(item["identity"]) for item in entries]
    if len({item["install_prefix"] for item in identities}) != len(identities):
        raise ValueError("duplicate delivery prefix")
    for identity in identities:
        if identity["stack_digest"]:
            companion_for(identity, identities)
    for item, identity in zip(entries, identities):
        package_native_modules(item, root=root,
                               allowed_prefixes=compatible_prefixes(identity, identities))
    manifest = inventory(entries, root)
    for item in manifest["entries"]:
        prefix = item["identity"]["install_prefix"].lstrip("/")
        local = {"schema": 2, "entry": item,
                 "files": [row for row in manifest["files"] if beneath(row["path"], [prefix])]}
        path = root / prefix / MANIFEST_PATH
        content = canonical(local).encode()
        if path.is_symlink():
            raise ValueError("manifest must not be a symlink")
        if path.exists():
            if not path.is_file() or path.read_bytes() != content or stat.S_IMODE(path.stat().st_mode) != 0o444:
                raise ValueError("existing manifest conflicts with installed inventory")
        else:
            with path.open("xb") as output:
                output.write(content)
            path.chmod(0o444)
    return manifest


def validate_packaged_modules(manifest):
    """Require the delivered Tcl to match trusted declarative generation."""
    identities = [item["identity"] for item in manifest["entries"]]
    indexed = {row["path"]: row for row in manifest["files"]}
    for item in manifest["entries"]:
        identity = item["identity"]
        prefix = identity["install_prefix"].lstrip("/")
        modules = {
            FRAGMENT_PATH: render_native_fragment(item, allowed_prefixes=compatible_prefixes(identity, identities)),
            f'modulefiles/{identity["software"]}/{identity["track"]}/{identity["build_id"]}':
                render_native_selector(identity),
        }
        for relative, content in modules.items():
            data = content.encode()
            expected = {"path": prefix + "/" + relative, "kind": "file", "mode": 0o444,
                        "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if indexed.get(expected["path"]) != expected:
                raise ValueError("embedded native module differs from trusted declarative generation")


def aggregate_manifests(manifests):
    """Validate prefix-local schema 2 inventories as one internal schema 1 set."""
    from release_contract import validate_identity
    entries, files = [], []
    for local in manifests:
        if (not isinstance(local, dict) or set(local) != {"schema", "entry", "files"} or
                type(local["schema"]) is not int or local["schema"] != 2 or
                not isinstance(local["entry"], dict) or not isinstance(local["files"], list)):
            raise ValueError("invalid per-install delivery manifest schema")
        identity = validate_identity(local["entry"].get("identity"))
        prefix = identity["install_prefix"].lstrip("/")
        if any(not isinstance(row, dict) or not beneath(relative_path(row.get("path")), [prefix])
               for row in local["files"]):
            raise ValueError("per-install manifest contains another installation's files")
        entries.append(local["entry"])
        files.extend(local["files"])
    aggregate = validate_manifest({"schema": 1, "entries": entries, "files": files})
    validate_packaged_modules(aggregate)
    return aggregate


def read_installed_manifests(entries, root=Path("/")):
    """Read only expected prefix-local manifests, without executing any code."""
    from release_contract import validate_identity
    root = Path(root).resolve(strict=True)
    identities = [validate_identity(entry["identity"]) for entry in entries]
    local = []
    for identity in identities:
        path = root / identity["install_prefix"].lstrip("/") / MANIFEST_PATH
        if path.resolve(strict=True) != path or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o444:
            raise ValueError("installed manifest requires regular, non-symlink path and mode 0444")
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("embedded delivery manifest exceeds size limit")
        item = parse_manifest(path.read_bytes())
        if (not isinstance(item, dict) or not isinstance(item.get("entry"), dict) or
                item["entry"].get("identity") != identity):
            raise ValueError("installed manifest identity differs from expected prefix")
        local.append(item)
    aggregate = aggregate_manifests(local)
    expected = [validate_native_entry(entry, allowed_prefixes=compatible_prefixes(identity, identities))
                for entry, identity in zip(entries, identities)]
    if aggregate["entries"] != expected:
        raise ValueError("installed manifest entry differs from expected native entry")
    return aggregate


def required_prefixes(entry, manifest):
    """Other bundled installs actually required by this one, not other targets."""
    identity = entry["identity"]
    identities = [item["identity"] for item in manifest["entries"]]
    required = set()
    if identity["stack_digest"]:
        required.add(companion_for(identity, identities)["install_prefix"])
    paths = [value for values in entry["runtime"]["prepend"].values() for value in values]
    paths.extend(value for value in entry["runtime"]["set"].values() if value.startswith("/"))
    indexed = {row["path"]: row for row in manifest["files"]}
    for row in manifest["files"]:
        if row["kind"] == "symlink" and beneath("/" + row["path"], [identity["install_prefix"]]):
            paths.append(resolve_inventory_path("/" + row["path"], indexed,
                         compatible_prefixes(identity, identities), entry["external_roots"]))
    for peer in identities:
        prefix = peer["install_prefix"]
        if prefix != identity["install_prefix"] and any(beneath(path, [prefix]) for path in paths):
            required.add(prefix)
    return sorted(required)


class SquashfsReader:
    def __init__(self, image, offset):
        self.image = Path(image)
        self.offset = offset
        self.manifest_records = {}

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

    def read_manifest(self, path):
        with subprocess.Popen(self.command([path]), stdout=subprocess.PIPE) as process:
            data = process.stdout.read(MAX_MANIFEST_BYTES + 1)
            if len(data) > MAX_MANIFEST_BYTES:
                process.kill()
                raise ValueError("embedded delivery manifest exceeds size limit")
            if process.wait() != 0:
                raise ValueError("cannot read embedded delivery manifest")
        return parse_manifest(data), data

    def manifest(self, prefixes=None):
        """Discover only per-install manifests; never extract or execute rootfs."""
        from release_contract import validate_identity
        listing = subprocess.check_output(
            ["unsquashfs", "-l", "-d", "/image", "-no-progress", "-o", str(self.offset),
             str(self.image), "opt/software/*/*/*/*/" + MANIFEST_PATH],
            text=True, env=dict(os.environ, LC_ALL="C"))
        paths = []
        for line in listing.splitlines():
            if line.startswith("/image/opt/software/") and line.endswith("/" + MANIFEST_PATH):
                path = relative_path(line.removeprefix("/image/"))
                if not re.fullmatch(r"opt/software/[^/]+/[^/]+/[^/]+/[^/]+/" + MANIFEST_PATH, path):
                    raise ValueError("manifest is not in a canonical installation directory")
                if path in paths:
                    raise ValueError("duplicate embedded manifest listing")
                paths.append(path)
        if not paths:
            raise ValueError("no per-install delivery manifests found")
        locals_ = []
        self.manifest_records = {}
        for path in sorted(paths):
            local, data = self.read_manifest(path)
            if (not isinstance(local, dict) or set(local) != {"schema", "entry", "files"} or
                    type(local["schema"]) is not int or local["schema"] != 2 or
                    not isinstance(local["entry"], dict) or not isinstance(local["files"], list)):
                raise ValueError("invalid per-install delivery manifest schema")
            identity = validate_identity(local["entry"].get("identity"))
            prefix = identity["install_prefix"].lstrip("/")
            if path != prefix + "/" + MANIFEST_PATH:
                raise ValueError("manifest location differs from canonical identity prefix")
            if any(not isinstance(row, dict) or not beneath(relative_path(row.get("path")), [prefix])
                   for row in local["files"]):
                raise ValueError("per-install manifest contains another installation's files")
            locals_.append(local)
            self.manifest_records[path] = {"path": path, "kind": "file", "mode": 0o444,
                                           "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        aggregate = aggregate_manifests(locals_)
        if prefixes is not None:
            if not isinstance(prefixes, (list, tuple)) or not prefixes or any(not isinstance(p, str) for p in prefixes):
                raise ValueError("selected prefixes must be a nonempty list of canonical install prefixes")
            selected = set(prefixes)
            identities = [item["identity"] for item in aggregate["entries"]]
            if not selected <= {item["install_prefix"] for item in identities}:
                raise ValueError("selected prefix is absent or not a canonical install prefix")
            for identity in identities:
                if identity["install_prefix"] in selected and identity["stack_digest"]:
                    selected.add(companion_for(identity, identities)["install_prefix"])
            aggregate = validate_manifest({
                "schema": 1,
                "entries": [item for item in aggregate["entries"] if item["identity"]["install_prefix"] in selected],
                "files": [row for row in aggregate["files"] if beneath("/" + row["path"], selected)],
            })
        return aggregate

    def selected_manifest_records(self, manifest):
        return [self.manifest_records[item["identity"]["install_prefix"].lstrip("/") + "/" + MANIFEST_PATH]
                for item in manifest["entries"]]

    def validate_metadata(self, manifest, *, selected_link=None):
        """Check real file boundaries/types before concatenated -cat reads.

        Without this check, a forged size could move bytes from one file to
        another while preserving the overall concatenated stream checksum.
        Match complete expected listing tails, not whitespace-split filenames.
        """
        roots = ([selected_link["path"]] if selected_link else
                 [entry["identity"]["install_prefix"].lstrip("/") for entry in manifest["entries"]])
        records = ([selected_link] if selected_link else
                   manifest["files"] + self.selected_manifest_records(manifest))
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


def export_image(image, expected_sha256, destination, *, prefixes=None, reader_factory=SquashfsReader.from_sif):
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
    manifest = validate_manifest(reader.manifest(prefixes=prefixes))
    validate_packaged_modules(manifest)
    reader.validate_metadata(manifest)
    prefixes = [entry["identity"]["install_prefix"] for entry in manifest["entries"]]
    manifest_records = reader.selected_manifest_records(manifest)
    # TemporaryDirectory only removes the exact newly-created private staging
    # directory on failure. No user destination or existing software is removed.
    with tempfile.TemporaryDirectory(prefix=".sai-native-export-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "bundle"
        root = Path(temporary) / "private-extraction"
        root.mkdir(parents=True, mode=0o755)
        for row in manifest["files"]:
            if row["kind"] == "directory":
                (root / row["path"]).mkdir(parents=True, exist_ok=True, mode=0o755)
        reader.copy_files([row for row in manifest["files"] if row["kind"] == "file"] + manifest_records, root)
        # Links are created last, never traversed during extraction or hashing.
        for row in manifest["files"]:
            if row["kind"] == "symlink":
                (root / row["path"]).symlink_to(row["target"])
        installations = []
        for entry in manifest["entries"]:
            identity = entry["identity"]
            prefix = identity["install_prefix"]
            base = root / prefix.lstrip("/")
            exported_path = ("." if len(prefixes) == 1 else
                             str(PurePosixPath(prefix).relative_to("/opt/software")))
            local_manifest = next(row for row in manifest_records
                                  if row["path"] == prefix.lstrip("/") + "/" + MANIFEST_PATH)
            partners = [peer for peer in prefixes if peer != prefix]
            required = required_prefixes(entry, manifest)
            receipt = {
                "schema": 2, "image_sha256": expected_sha256,
                "manifest_sha256": local_manifest["sha256"],
                "expected_install_prefix": prefix,
                "bundle_install_prefixes": prefixes,
                "other_bundle_install_prefixes": partners,
                "required_install_prefixes": required,
                "external_roots": entry["external_roots"],
                "dependency_modules": entry["runtime"]["modules"],
                "module_use": prefix + "/modulefiles",
                "module_load": f'{identity["software"]}/{identity["track"]}/{identity["build_id"]}',
                "validation": "integrity-checked; native science and performance acceptance still required",
                "relocation": "staging only; install at expected /opt prefixes or separately validate relocation",
            }
            readme = (
                "This complete installation folder is a staging export, not a privileged installer.\n"
                "Copy this WHOLE folder to the expected absolute prefix:\n  " + prefix + "\n"
                "The original manifest and native modules are already inside this folder.\n"
                "Required companion/installation dependencies (if any):\n" +
                "".join("  " + peer + "\n" for peer in required) +
                "Other bundled partitions are independent, not dependencies of this installation.\n" +
                "After installing at the expected prefix:\n  module use " + receipt["module_use"] +
                "\n  module load " + receipt["module_load"] + "\n"
                "Load inside a matching Slurm allocation. Do not substitute a staging path for /opt.\n"
                "Recorded external site dependency modules must remain available.\n"
                "This export does not certify native scientific results, speed, or arbitrary relocation.\n")
            for relative, content in ((EXPORT_PATH, canonical(receipt)), (README_PATH, readme)):
                path = base / relative
                with path.open("x") as output:
                    output.write(content)
                path.chmod(0o444)
            installations.append({"path": exported_path, **receipt})
        # Restore inventoried directory modes only after writing the receipts.
        for row in reversed(manifest["files"]):
            if row["kind"] == "directory":
                (root / row["path"]).chmod(row["mode"])
        if len(prefixes) == 1:
            (root / prefixes[0].lstrip("/")).rename(stage)
        else:
            stage.mkdir(mode=0o755)
            for item in installations:
                output = stage / item["path"]
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                parent = output.parent
                while parent != stage:
                    parent.chmod(0o755)
                    parent = parent.parent
                (root / item["expected_install_prefix"].lstrip("/")).rename(output)
        receipt = {"schema": 2, "image_sha256": expected_sha256,
                   "expected_install_prefixes": prefixes, "installations": installations,
                   "validation": "integrity-checked; native science and performance acceptance still required"}
        if checksum(image) != expected_sha256:
            raise ValueError("image changed during export")
        # Reserve destination atomically; never replace an existing user path.
        destination.mkdir(mode=0o700)
        try:
            for child in stage.iterdir():
                child.rename(destination / child.name)
            destination.chmod(stat.S_IMODE(stage.stat().st_mode))
        except BaseException:
            # Leave any already moved files recoverable; never recursively
            # remove a destination once exposed outside our private staging.
            raise RuntimeError(f"incomplete export preserved at {destination}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="inside build container: package modules and write each prefix's manifest")
    inv.add_argument("--entry", type=Path, action="append", required=True)
    inv.add_argument("--root", type=Path, default=Path("/"), help="optional staging root; canonical prefixes do not change")
    exp = sub.add_parser("export", help="stage a SIF's declared installed trees without executing it")
    exp.add_argument("image", type=Path)
    exp.add_argument("--image-sha256", required=True)
    exp.add_argument("--destination", type=Path, required=True)
    exp.add_argument("--prefix", action="append", help="select a canonical /opt prefix; locked companions are automatic")
    args = parser.parse_args()
    if args.command == "inventory":
        write_manifests([json.loads(path.read_text()) for path in args.entry], root=args.root)
    else:
        print(canonical(export_image(args.image, args.image_sha256, args.destination, prefixes=args.prefix)), end="")


if __name__ == "__main__":
    main()
