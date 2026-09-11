#!/usr/bin/env python3
"""Resolve the two moving upstreams as one immutable, ABI-tested build unit."""
import argparse
import hashlib
import json
from pathlib import Path
from remote_controller import safe_name, safe_sha
from resolve_source import resolve

REPOSITORIES = {"deepmd-kit": "deepmodeling/deepmd-kit", "lammps": "lammps/lammps"}
TARGETS = ("4v100-avx512", "16v100-avx2", "8v100v0-avx512")


def pair(deepmd_ref, lammps_ref, target, resolver=resolve):
    if target not in TARGETS:
        raise ValueError("MD target has no feature/ABI acceptance policy")
    sources = {name: resolver(repository, ref) for name, repository, ref in (
        ("deepmd-kit", REPOSITORIES["deepmd-kit"], deepmd_ref),
        ("lammps", REPOSITORIES["lammps"], lammps_ref))}
    for name, source in sources.items():
        safe_sha(source["sha"])
        safe_name(source["version"])
        source["repository"] = REPOSITORIES[name]
    label = "dp-" + sources["deepmd-kit"]["sha"][:12] + "-lmp-" + sources["lammps"]["sha"][:12]
    return {"schema": 1, "version": label, "target": target, "sources": sources}


def resolve_track_pairs(tracks=("development", "prerelease", "release"), targets=TARGETS,
                        resolver=resolve):
    """Resolve each component's channels independently, then lock companions.

    A resolved primary always gets a pair if its partner has the same channel
    or, only on precise channel absence, a stable release. Both components keep
    their actual track. API/ref errors propagate; they never select a fallback.
    This schema-2 planning API is intentionally distinct from legacy ``pair``;
    callers must migrate the complete build/runtime chain before submitting it.
    """
    from release_contract import TRACKS, resolve_tracks
    if (not isinstance(tracks, (list, tuple)) or not tracks or len(set(tracks)) != len(tracks) or
            any(track not in TRACKS for track in tracks)):
        raise ValueError("invalid MD release tracks")
    if (not isinstance(targets, (list, tuple)) or not targets or len(set(targets)) != len(targets) or
            any(target not in TARGETS for target in targets)):
        raise ValueError("invalid MD release targets")
    queried = list(tracks)
    if "prerelease" in tracks and "release" not in queried:
        queried.append("release")
    rows = {software: {row["track"]: row for row in resolve_tracks(software, queried, resolver)}
            for software in REPOSITORIES}
    plans, skipped = {}, []
    for primary in REPOSITORIES:
        companion = "lammps" if primary == "deepmd-kit" else "deepmd-kit"
        for track in tracks:
            source = rows[primary][track]
            trigger = {"software": primary, "track": track}
            if source["status"] == "skipped":
                skipped.append(dict(trigger, reason=source["reason"]))
                continue
            partner = rows[companion][track]
            fallback = partner["status"] == "skipped"
            if fallback:
                partner = rows[companion].get("release", partner)
            if partner["status"] != "resolved":
                skipped.append(dict(trigger, reason=f"{companion} has neither {track} nor a stable release companion"))
                continue
            selected = {primary: source, companion: partner}
            sources = {software: {"repository": selected[software]["repository"],
                                  "ref": selected[software]["source_ref"],
                                  "sha": selected[software]["source_sha"],
                                  "version": selected[software]["source_version"],
                                  "track": selected[software]["track"]}
                       for software in REPOSITORIES}
            label = "dp-" + sources["deepmd-kit"]["track"] + "-" + sources["deepmd-kit"]["sha"][:12]
            label += "-lmp-" + sources["lammps"]["track"] + "-" + sources["lammps"]["sha"][:12]
            for target in targets:
                key = json.dumps({"sources": sources, "target": target}, sort_keys=True, separators=(",", ":"))
                if key not in plans:
                    plans[key] = {"schema": 2, "version": safe_name(label), "target": target,
                                  "sources": sources, "selection_sha256": hashlib.sha256(key.encode()).hexdigest(),
                                  "triggers": []}
                plans[key]["triggers"].append(dict(trigger, companion_selection=(
                    "latest_release_fallback" if fallback else "same_track")))
    return {"pairs": list(plans.values()), "skipped": skipped}


def identify_track_pair(plan, recipe_sha256):
    """Use the actual deployed recipe digest to create both canonical prefixes."""
    from release_contract import make_identity
    if (not isinstance(plan, dict) or set(plan) != {
            "schema", "version", "target", "sources", "selection_sha256", "triggers"} or
            type(plan["schema"]) is not int or plan["schema"] != 2 or plan["target"] not in TARGETS or
            not isinstance(plan["sources"], dict) or set(plan["sources"]) != set(REPOSITORIES)):
        raise ValueError("expected an exact schema-2 tracked MD pair")
    key = json.dumps({"sources": plan["sources"], "target": plan["target"]}, sort_keys=True, separators=(",", ":"))
    if plan["selection_sha256"] != hashlib.sha256(key.encode()).hexdigest():
        raise ValueError("MD source selection changed")
    locked = {software: safe_sha(source["sha"]) for software, source in plan["sources"].items()}
    identities = {}
    for software, source in plan["sources"].items():
        if (not isinstance(source, dict) or set(source) != {"repository", "ref", "sha", "version", "track"} or
                source["repository"] != REPOSITORIES[software]):
            raise ValueError("tracked MD source repository or schema differs")
        identities[software] = make_identity(software, source["track"], source["ref"], source["sha"],
                                             source["version"], recipe_sha256, plan["target"], stack_sources=locked)
    expected_label = "dp-" + plan["sources"]["deepmd-kit"]["track"] + "-" + locked["deepmd-kit"][:12]
    expected_label += "-lmp-" + plan["sources"]["lammps"]["track"] + "-" + locked["lammps"][:12]
    if plan["version"] != expected_label:
        raise ValueError("tracked MD pair label differs from its channels and sources")
    if not isinstance(plan["triggers"], list) or not plan["triggers"]:
        raise ValueError("tracked MD pair needs an explicit trigger")
    for trigger in plan["triggers"]:
        if (not isinstance(trigger, dict) or set(trigger) != {"software", "track", "companion_selection"} or
                trigger["software"] not in identities or
                trigger["track"] != identities[trigger["software"]]["track"]):
            raise ValueError("tracked MD trigger differs from selected primary")
        peer = "lammps" if trigger["software"] == "deepmd-kit" else "deepmd-kit"
        peer_track = identities[peer]["track"]
        mode = "same_track" if peer_track == trigger["track"] else "latest_release_fallback"
        if (trigger["companion_selection"] != mode or
                (mode == "latest_release_fallback" and peer_track != "release")):
            raise ValueError("tracked MD companion is not from the declared selection policy")
    return identities


def fingerprint(control=None):
    control = Path(control or __file__).resolve()
    if control.is_file():
        control = control.parent
    names = [*sorted(p.name for p in control.glob("md_*.*") if p.suffix in (".py", ".sh", ".json")),
             "remote_controller.py", "source_cache.py", "create_rootfs.sh", "resolve_source.py"]
    digest = hashlib.sha256(b"sai-deepmd-lammps-contract-v1\n")
    for name in names:
        path = control / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("untrusted MD controller file")
        digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--deepmd-ref", default="master")
    parser.add_argument("--lammps-ref", default="develop")
    parser.add_argument("--target", choices=TARGETS, default=TARGETS[0])
    args = parser.parse_args()
    print(json.dumps(pair(args.deepmd_ref, args.lammps_ref, args.target), sort_keys=True))
