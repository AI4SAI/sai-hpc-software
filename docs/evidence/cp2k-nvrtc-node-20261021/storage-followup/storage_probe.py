#!/usr/bin/env python3
"""Read-only comparison of buffered and O_DIRECT reads of the NVRTC libraries."""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def read_digest(path, direct=False):
    start = time.monotonic()
    command = ["dd", f"if={path}", "bs=1M", "status=none"]
    if direct:
        command.append("iflag=direct")
    digest = hashlib.sha256()
    size = 0
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        while data := child.stdout.read(1024 * 1024):
            digest.update(data)
            size += len(data)
        error = child.stderr.read().decode(errors="replace")
        status = child.wait()
    return {"direct": direct, "sha256": digest.hexdigest() if status == 0 else None,
            "bytes": size, "exit": status, "stderr": error,
            "seconds": time.monotonic() - start}


def metadata(path):
    stat = path.stat()
    return {"inode": stat.st_ino, "device": stat.st_dev, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}


def main():
    result = {"hostname": socket.gethostname(), "job_id": os.environ.get("SLURM_JOB_ID"),
              "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": []}
    for root in ["/opt/devtools/nvidia/cuda-12.9.1/targets/x86_64-linux/lib",
                 "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/targets/x86_64-linux/lib"]:
        path = Path(root) / "libnvrtc.so.12.9.86"
        record = {"path": str(path), "resolved": str(path.resolve())}
        try:
            record["before"] = metadata(path)
            mount = subprocess.run(["findmnt", "--json", "-T", str(path), "-o",
                                    "TARGET,SOURCE,FSTYPE,OPTIONS"], capture_output=True, text=True)
            record["mount"] = json.loads(mount.stdout) if mount.returncode == 0 else mount.stderr
            record["reads"] = [read_digest(path), read_digest(path, direct=True), read_digest(path)]
            record["after"] = metadata(path)
        except (OSError, ValueError) as error:
            record["error"] = str(error)
        result["files"].append(record)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
