# sai-hpc-software

Containerized, reproducible builds for HPC software on the SAI Slurm cluster.

Each target in a build plan is compiled independently in Apptainer after GitHub Actions connects to `SAI-stardust`. Source, build, install, and result paths are separated; project commands never run on the host.

## Secrets

Configure `REMOTE_USER`, `REMOTE_SSH_PRIVATE_KEY`, and `SAI_SSH_KNOWN_HOSTS` as repository Actions secrets/variables. Use a dedicated CI key whose public key is installed on the cluster.

## Local validation

```sh
python3 controller/validate_plan.py plans/cp2k.example.json --output /tmp/validated.json
python3 -m unittest discover -s tests
```

The administrator may move an artifact's installation tree to `/opt`; deployment is intentionally outside this repository.
