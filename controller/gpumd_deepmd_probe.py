#!/usr/bin/env python3
"""Create a tiny local PT model, then independently evaluate its energy/forces.

Executed only in the GPU acceptance container with the site's DeepMD Python.
This checks interface numerics, not the accuracy of a one-step fitted potential.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import numpy as np


def main():
    task = Path(sys.argv[1]).resolve()
    task.mkdir(exist_ok=True)
    rows = (task / "model.xyz").read_text().splitlines()
    count = int(rows[0])
    coords = np.array([[float(x) for x in line.split()[1:4]] for line in rows[2:2 + count]])
    if any(line.split()[0] != "Cu" for line in rows[2:2 + count]):
        raise ValueError("upstream DeepMD probe no longer contains the Cu-only model")
    cell = np.array([float(value) for value in re.search(r'Lattice="([^"]+)"', rows[1]).group(1).split()]).reshape(3, 3)
    data = task / "data"
    (data / "set.000").mkdir(parents=True)
    (data / "type.raw").write_text("0\n" * count)
    (data / "type_map.raw").write_text("Cu\n")
    for name, array in {"coord": coords.reshape(1, -1), "box": cell.reshape(1, -1),
                        "energy": np.array([0.0]), "force": np.zeros((1, 3 * count))}.items():
        np.save(data / "set.000" / f"{name}.npy", array)
    config = {
        "model": {"type_map": ["Cu"], "descriptor": {"type": "se_e2_a", "sel": [32],
                  "rcut": 3.0, "rcut_smth": 2.5, "neuron": [4, 8, 16],
                  "axis_neuron": 4, "seed": 42}, "fitting_net": {"neuron": [8, 8], "seed": 42}},
        "learning_rate": {"type": "exp", "start_lr": 0.001, "stop_lr": 0.0001, "decay_steps": 1},
        "loss": {"type": "ener", "start_pref_e": 1, "limit_pref_e": 1,
                 "start_pref_f": 1, "limit_pref_f": 1},
        "training": {"training_data": {"systems": ["data"], "batch_size": 1},
                     "numb_steps": 1, "seed": 42, "disp_freq": 1, "save_freq": 1},
    }
    (task / "input.json").write_text(json.dumps(config))
    for args in (("train", "input.json", "--skip-neighbor-stat"),
                 ("freeze", "-o", "frozen_model.pth")):
        subprocess.run([sys.executable, "-m", "deepmd", "--pt", *args], cwd=task,
                       check=True, timeout=300)
    from deepmd.infer import DeepPot
    evaluator = DeepPot(str(task / "frozen_model.pth"))
    energy, force, virial = evaluator.eval(coords.reshape(1, -1), cell.reshape(1, -1), [0] * count)
    reference = {"energy": float(energy.ravel()[0]), "forces": force.reshape(count, 3).tolist(),
                 "virial": virial.reshape(9).tolist(), "backend": "pytorch", "n_atoms": count}
    (task / "reference.json").write_text(json.dumps(reference, sort_keys=True))


if __name__ == "__main__":
    main()
