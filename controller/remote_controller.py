#!/usr/bin/env python3
"""Pure, host-side path and container policy used by the SAI controller."""
from pathlib import Path, PurePosixPath

ROOT_NAME = "sai-hpc-software"
def layout(home: str, run_id: str) -> dict[str, Path]:
    root=Path(home)/ROOT_NAME; run=root/"runs"/run_id
    if not run_id or Path(run_id).name != run_id or ".." in Path(run_id).parts: raise ValueError("invalid run id")
    return {"root":root,"cache":root/"cache","controller":root/"controller","source":run/"source","build":run/"build","install":run/"install","results":run/"results","tmp":run/"tmp"}
def container_command(paths: dict[str,Path], image: str, command: list[str]) -> list[str]:
    if not Path(image).is_absolute() or not command or any("\x00" in x for x in command): raise ValueError("invalid image or command")
    binds=[(paths["source"],"/workspace/source","ro"),(paths["build"],"/workspace/build","rw"),(paths["install"],"/workspace/install","rw"),(paths["results"],"/workspace/results","rw"),(paths["tmp"],"/tmp","rw"),(Path("/opt"),"/opt","ro"),(Path("/usr"),"/usr","ro"),(Path("/lib"),"/lib","ro"),(Path("/lib64"),"/lib64","ro")]
    out=["apptainer","exec","--cleanenv","--containall","--no-home"]
    out += [item for src,dst,mode in binds for item in ("--bind",f"{src}:{dst}:{mode}")]
    return out+[image,"/bin/sh","-c","cd /workspace/source && exec \"$@\"","sh",*command]
