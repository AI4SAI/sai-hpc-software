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

## SAI home-directory layout

The remote root is `~/sai-hpc-software`. Containers are kept separately from runs and caches:

```text
containers/base/minimal-v1/       # shared minimal rootfs
containers/software/<name>/<version>/<target>/  # optional software images
cache/repositories/<name>/        # bare Git source cache
runs/<run-id>/{source,results,tmp} # ephemeral per-run data
artifacts/                        # exported installation archives
```

The legacy `images/minimal-v1` path is a compatibility symlink to `containers/base/minimal-v1`. Build containers never receive a writable bind of the cache or controller.

Source transfer uses an ABACUS-style Git bundle cache under `~/sai-hpc-software/cache/repositories`. A fixed controller creates an exact-commit bundle, splits it into eight parts, verifies SHA-256, and updates the bare cache. The cache is never writable from the build container; the checked-out source tree is bound read-only. All cache and task paths remain below `~/sai-hpc-software`, never the SAI host `/tmp`.
