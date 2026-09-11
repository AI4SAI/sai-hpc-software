"""Deterministic source identities for partitioned software delivery.

This module resolves upstream references read-only. It does not build, publish,
or grant scientific acceptance. Paired DeepMD/LAMMPS deliveries must pass both
exact commits through ``stack_sources``; standalone deliveries may omit it.
"""
import hashlib
import json
import re

from remote_controller import TARGETS
from resolve_source import resolve as _resolve_source


SCHEMA = 1
TRACKS = ("development", "prerelease", "release")
PARTITIONS = {
    "dsprhbm": "DSPRHBM",
    "4v100-avx512": "4V100",
    "16v100-avx2": "16V100",
    "8v100v0-avx512": "8V100V0",
}
GPU_TARGETS = tuple(target for target in PARTITIONS if target != "dsprhbm")
SOFTWARE = {
    "abacus": {"repository": "deepmodeling/abacus-develop", "development_ref": "develop",
               "targets": tuple(PARTITIONS)},
    "cp2k": {"repository": "cp2k/cp2k", "development_ref": "master",
             "targets": tuple(PARTITIONS)},
    "gpumd": {"repository": "brucefan1983/GPUMD", "development_ref": "master",
              "targets": GPU_TARGETS},
    "deepmd-kit": {"repository": "deepmodeling/deepmd-kit", "development_ref": "master",
                   "targets": GPU_TARGETS},
    "lammps": {"repository": "lammps/lammps", "development_ref": "develop",
               "targets": GPU_TARGETS},
}
STACK_SOFTWARE = frozenset(("deepmd-kit", "lammps"))
MAX_VERSION_LABEL = 64
MAX_BUILD_ID = 106


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def _sha(value, length, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % length, value):
        raise ValueError(f"{field} must be an exact lowercase {length}-digit SHA")
    return value


def _software(software):
    if not isinstance(software, str) or software not in SOFTWARE:
        raise ValueError("unregistered software")
    return SOFTWARE[software]


def _track(track):
    if not isinstance(track, str) or track not in TRACKS:
        raise ValueError("unregistered release track")
    return track


def allowed_partitions(software):
    """Return this software's permitted real Slurm partition names, in order."""
    return tuple(PARTITIONS[target] for target in _software(software)["targets"])


def _source_ref(value):
    if (not isinstance(value, str) or len(value) > 512 or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./+@-]*", value) or
            ".." in value or "//" in value or value.endswith(("/", ".")) or
            any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))):
        raise ValueError("invalid source_ref")
    return value


def _version_label(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("source_version must contain 1 to 1024 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("source_version contains a control character")
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("._-")
    if not normalized:
        raise ValueError("source_version has no usable version label")
    # Preserve readable short upstream versions. Changed/truncated labels carry
    # their original spelling's digest, so e.g. v/1 and v?1 cannot alias. Reserve
    # that suffix for generated labels, including when an upstream name uses it.
    if (normalized != value or len(normalized) > MAX_VERSION_LABEL or
            re.search(r"-v[0-9a-f]{12}$", normalized)):
        suffix = "-v" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        normalized = normalized[:MAX_VERSION_LABEL - len(suffix)].rstrip("._-") + suffix
    return normalized


def _stack(software, source_sha, sources):
    if sources is None:
        sources = {}
    if not isinstance(sources, dict):
        raise ValueError("stack_sources must map software names to exact commits")
    if not sources:
        return {}, None
    if software not in STACK_SOFTWARE or set(sources) != STACK_SOFTWARE:
        raise ValueError("paired stack_sources must contain exactly deepmd-kit and lammps")
    locked = {name: _sha(sources[name], 40, f"stack_sources.{name}") for name in sorted(sources)}
    if locked[software] != source_sha:
        raise ValueError("paired stack source differs from the component's source_sha")
    return locked, hashlib.sha256(_canonical(locked).encode("ascii")).hexdigest()


def make_identity(software, track, source_ref, source_sha, source_version,
                  recipe_sha256, target, stack_sources=None):
    """Return schema 1 provenance and its canonical immutable install prefix.

    ``target`` is a registered architecture key, while the final prefix
    component is the real Slurm partition. ``stack_sources``, when supplied,
    maps both ``deepmd-kit`` and ``lammps`` to their full 40-digit commits.
    """
    profile = _software(software)
    _track(track)
    _source_ref(source_ref)
    _sha(source_sha, 40, "source_sha")
    _sha(recipe_sha256, 64, "recipe_sha256")
    if not isinstance(target, str) or target not in profile["targets"]:
        raise ValueError("target is not registered for this software")
    hardware = TARGETS[target]
    if hardware["partition"] != PARTITIONS[target]:
        raise ValueError("target partition no longer matches the delivery contract")
    label = _version_label(source_version)
    stack, stack_digest = _stack(software, source_sha, stack_sources)
    build_id = f"{label}-g{source_sha[:12]}-r{recipe_sha256[:12]}"
    if stack_digest:
        build_id += "-s" + stack_digest[:12]
    if len(build_id) > MAX_BUILD_ID:
        raise ValueError("build_id exceeds the delivery name limit")
    return {
        "schema": SCHEMA, "software": software, "track": track,
        "repository": profile["repository"], "source_ref": source_ref,
        "source_sha": source_sha, "source_version": source_version,
        "version_label": label, "recipe_sha256": recipe_sha256,
        "target": target, "partition": PARTITIONS[target],
        "cpu_arch": hardware["cpu_arch"], "dependency_isa": hardware["dependency_isa"],
        "cuda_arch": hardware["cuda_arch"], "gpus": hardware["gpus"],
        "build_id": build_id,
        "install_prefix": f"/opt/software/{software}/{track}/{build_id}/{PARTITIONS[target]}",
        "stack_sources": stack, "stack_digest": stack_digest,
    }


def validate_identity(record):
    """Recompute every field, rejecting altered paths, schema, or extra fields.

    This checks internal identity consistency, not scientific acceptance or an
    external signature. Return a fresh canonical record on success.
    """
    if not isinstance(record, dict):
        raise ValueError("identity must be a schema 1 object")
    try:
        expected = make_identity(**{name: record[name] for name in (
            "software", "track", "source_ref", "source_sha", "source_version",
            "recipe_sha256", "target", "stack_sources")})
        # JSON comparison distinguishes bool/int and int/float schema changes.
        if _canonical(record) != _canonical(expected):
            raise ValueError("identity does not match its canonical delivery contract")
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("malformed release identity") from error
    return expected


def resolve_tracks(software, tracks=TRACKS, resolver=_resolve_source):
    """Resolve requested channels live, returning ordered resolved/skipped rows.

    Only the resolver's precise no-release/no-prerelease messages are optional
    channel absence. Network failures and all other resolution errors propagate.
    The caller decides whether a skipped channel is acceptable for its workflow.
    """
    profile = _software(software)
    if isinstance(tracks, (str, bytes)):
        raise ValueError("tracks must be a sequence of release track names")
    try:
        tracks = list(tracks)
    except TypeError as error:
        raise ValueError("tracks must be a sequence of release track names") from error
    if not tracks:
        raise ValueError("at least one release track is required")
    for track in tracks:
        _track(track)
    if len(set(tracks)) != len(tracks):
        raise ValueError("duplicate release track")
    rows = []
    for track in tracks:
        requested = profile["development_ref"] if track == "development" else "latest-" + track
        row = {"software": software, "track": track, "repository": profile["repository"],
               "requested_ref": requested}
        try:
            resolved = resolver(profile["repository"], requested)
        except ValueError as error:
            if track in ("prerelease", "release") and str(error) == f"no {requested} available":
                rows.append(dict(row, status="skipped", reason=str(error)))
                continue
            raise
        if not isinstance(resolved, dict):
            raise ValueError("resolver returned a malformed source record")
        try:
            sha = _sha(resolved["sha"], 40, "resolved source_sha")
            ref = _source_ref(resolved["ref"])
            version = resolved["version"]
            _version_label(version)
        except (KeyError, TypeError) as error:
            raise ValueError("resolver returned an incomplete source record") from error
        rows.append(dict(row, status="resolved", source_ref=ref, source_sha=sha,
                         source_version=version))
    return rows
