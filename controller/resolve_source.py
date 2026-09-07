#!/usr/bin/env python3
"""Resolve a live branch/tag or the newest release channel, preserving upstream SHA."""
import argparse
import json
import re
import subprocess

def resolve(repository, ref):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    channel = ref
    if ref in ("latest-release", "latest-prerelease"):
        releases = json.loads(subprocess.check_output(
            ["gh", "api", f"repos/{repository}/releases?per_page=100"], text=True))
        candidates = [r for r in releases if not r["draft"] and
                      r["prerelease"] == (ref == "latest-prerelease")]
        if not candidates:
            raise ValueError(f"no {ref} available")
        ref = max(candidates, key=lambda r: r["published_at"])["tag_name"]
    refs = subprocess.check_output(
        ["git", "ls-remote", f"https://github.com/{repository}.git",
         f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"], text=True)
    mapping = {line.split()[1]: line.split()[0] for line in refs.splitlines()}
    commit = mapping.get(f"refs/heads/{ref}") or mapping.get(f"refs/tags/{ref}^{{}}") or mapping.get(f"refs/tags/{ref}")
    if commit is None and re.fullmatch("[0-9a-f]{40}", ref):
        commit = ref
    if commit is None:
        raise ValueError(f"cannot resolve {ref}")
    label = re.sub("[^A-Za-z0-9_.-]", "-", channel).strip(".-")[:80]
    return {"sha": commit, "version": f"{label}-{commit[:12]}", "ref": ref}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("repository"); p.add_argument("ref")
    a = p.parse_args()
    print(json.dumps(resolve(a.repository, a.ref)))
