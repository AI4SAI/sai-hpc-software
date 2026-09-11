#!/usr/bin/env python3
"""Resolve a live branch/tag or the newest release channel, preserving upstream SHA."""
import argparse
import json
import re
import subprocess


def _releases(repository):
    """Read every release page; API or pagination failures must propagate."""
    response = subprocess.check_output(
        ["gh", "api", "--paginate", f"repos/{repository}/releases?per_page=100"], text=True)
    # gh --paginate emits consecutive JSON pages. Decode each whole document;
    # unlike --slurp this also works with the site's older GitHub CLI releases.
    decoder = json.JSONDecoder()
    offset, page_count, releases = 0, 0, []
    while offset < len(response):
        if response[offset].isspace():
            offset += 1
            continue
        page, offset = decoder.raw_decode(response, offset)
        if not isinstance(page, list):
            raise ValueError("invalid release API response")
        releases.extend(page)
        page_count += 1
    if not page_count:
        raise ValueError("empty release API response")
    for release in releases:
        if (not isinstance(release, dict) or type(release.get("draft")) is not bool or
                type(release.get("prerelease")) is not bool):
            raise ValueError("invalid release metadata")
        if not release["draft"] and (
                not isinstance(release.get("published_at"), str) or not release["published_at"] or
                not isinstance(release.get("tag_name"), str) or not release["tag_name"]):
            raise ValueError("published release is missing its date or tag")
    return releases


def resolve(repository, ref):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    release_channel = ref in ("latest-release", "latest-prerelease")
    if release_channel:
        releases = _releases(repository)
        candidates = [r for r in releases if not r["draft"] and
                      r["prerelease"] == (ref == "latest-prerelease")]
        if not candidates:
            raise ValueError(f"no {ref} available")
        ref = max(candidates, key=lambda r: r["published_at"])["tag_name"]
    refs = subprocess.check_output(
        ["git", "ls-remote", f"https://github.com/{repository}.git",
         f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"], text=True)
    mapping = {line.split()[1]: line.split()[0] for line in refs.splitlines()}
    tag_commit = mapping.get(f"refs/tags/{ref}^{{}}") or mapping.get(f"refs/tags/{ref}")
    # GitHub release tags must never resolve to a same-named branch. Ordinary
    # explicit branch/tag requests retain their established branch precedence.
    commit = tag_commit if release_channel else mapping.get(f"refs/heads/{ref}") or tag_commit
    if commit is None and not release_channel and re.fullmatch("[0-9a-f]{40}", ref):
        commit = ref
    if commit is None:
        raise ValueError(f"cannot resolve {ref}")
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("resolved source is not a full Git commit SHA")
    # Keep the existing result shape while making channel versions meaningful:
    # latest-release -> v3.10.0-<sha>, rather than latest-release-<sha>.
    label = re.sub("[^A-Za-z0-9_.-]", "-", ref).strip("._-")[:80]
    if not label:
        raise ValueError("resolved source has no usable version label")
    return {"sha": commit, "version": f"{label}-{commit[:12]}", "ref": ref}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("repository"); p.add_argument("ref")
    a = p.parse_args()
    print(json.dumps(resolve(a.repository, a.ref)))
