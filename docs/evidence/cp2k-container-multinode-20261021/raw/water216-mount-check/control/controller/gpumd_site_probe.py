#!/usr/bin/env python3
"""Optional short baseline preflight; run only inside an allocated GPU SIF."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from gpumd_science import BASELINE, prepare, numbers, xyz, compare


def main():
    if not os.environ.get("SLURM_JOB_ID") or not os.environ.get("CUDA_VISIBLE_DEVICES"):
        raise ValueError("site probe requires a Slurm GPU allocation")
    inputs = Path("/work/inputs")
    prepare(BASELINE, inputs)
    results = {}
    for kind, executable in (("static", "gpumd"), ("prediction", "nep"), ("throughput", "gpumd")):
        case = Path("/work") / kind
        shutil.copytree(inputs / kind, case)
        with (case / "run.log").open("w") as log:
            subprocess.run([BASELINE / "bin" / executable], cwd=case, check=True, timeout=90,
                           stdout=log, stderr=subprocess.STDOUT)
        if kind == "static":
            energy, force = xyz(case / "dump.xyz")
            gold_e, gold_f = xyz(case / "gold.xyz")
            results[kind] = {"energy": energy, "energy_error": compare([[energy]], [[gold_e]], 1e-3, "energy"),
                             "force_error": compare(force, gold_f, 1e-4, "force")}
        elif kind == "prediction":
            results[kind] = {name: compare(numbers(case / name), numbers(case / f"gold-{name}"), 2e-4, name)
                            for name in ("energy_train.out", "force_train.out", "virial_train.out")}
        else:
            results[kind] = (case / "run.log").read_text()
    for setting in ("off", "on"):
        case = Path("/work") / f"training-{setting}"
        shutil.copytree(inputs / "training", case)
        with (case / "nep.in").open("a") as stream:
            stream.write(f"nep_compile {setting}\n")
        with (case / "run.log").open("w") as log:
            subprocess.run([BASELINE / "bin/nep"], cwd=case, check=True, timeout=120,
                           stdout=log, stderr=subprocess.STDOUT)
        results[f"training-{setting}"] = numbers(case / "loss.out")
    results["jit_loss_error"] = compare(results["training-on"], results["training-off"], 2e-3, "JIT loss")
    print(json.dumps(results, indent=2))
    Path("/work/site-probe.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
