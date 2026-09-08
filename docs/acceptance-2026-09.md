# 真实集群验收记录（2026-09）

本记录对应公开仓库 `AI4SAI/sai-hpc-software` 的真实 GitHub Actions → SSH → Slurm → Apptainer 流程。运行均使用 SAI 上用户家目录中的项目根目录；源码、编译树、安装树和 SquashFS 组装过程都在单个 ext3 overlay 内完成。

## 精确 V100 构建与宿主 MPI 验收（2026-09-08）

上游 `develop` 被解析为 `149723287702677dc159c5e96fb98cbc3482f445`，版本目录为
`develop-149723287702`。commit `3065a55867835610b1cf6f08280381e243d1ff28` 的
GitHub Actions run
[`34189103202`](https://github.com/AI4SAI/sai-hpc-software/actions/runs/34189103202)
完成了 SSH、源码缓存、Slurm 构建、SIF 发布和双节点科学计算的完整流程：

| 目标 | 构建 Slurm / 节点 | SIF SHA-256 | 双节点运行 Slurm / 节点 | 最终能量 (eV) |
| --- | --- | --- | --- | --- |
| `4v100-avx512` | `1178480` / `4v100n23` | `a362054206c865f80fd1e0dec7522aaf16b37d9361ea6a24d3074d8cc3c4995e` | `1178916` / `4v100n15,4v100n16` | `-4869.7470519499920556` |
| `16v100-avx2` | `1178478` / `16v100n02` | `61ff6383cbbd4a6bcff25cf00475ccbe48a1d3d6cb0d4deb19226015fd19bc70` | `1178918` / `16v100n81,16v100n82` | `-4869.7470519499847796` |

成品是两个只读单文件 SIF：

```text
/home/stardust/sai-hpc-software/containers/software/abacus/develop-149723287702/
├── 4v100-avx512/34189103202-1-4v100-avx512-develop-149723287702.sif
└── 16v100-avx2/34189103202-1-16v100-avx2-develop-149723287702.sif
```

每个运行使用宿主 Open MPI 启动两个 rank，每个 rank 再执行固定 SHA 的可信 launcher，
以 `apptainer exec --nv` 进入同一目标 SIF。rank trace 证明两个不同节点分别使用一张
V100，并分别解析到 `openmpi-...-avx512` 和 `openmpi-...-avx2`。两个 PW SCF 计算均显示
`GPU ... (x2)`、`#SCF IS CONVERGED#`，并以 `MULTINODE_CONTAINER_MPI_VERIFIED` 结束。
宿主 PMIx session 位于项目的 `runtime-tests/.../mpi-runtime`，按同一绝对路径 bind
进容器；没有使用宿主 `/tmp`。

构建成功后两个 `work.ext3` 均已删除。验收覆盖 CUDA-aware MPI、两节点/两 GPU、
目标 ISA、SIF 固定和数值结果；该小算例不走 ELPA，也不证明 ABACUS NCCL collective。

## 历史验收

| 目标 | GitHub Actions | Slurm | 结果 |
| --- | --- | --- | --- |
| `cpu-misc` | [34124103777](https://github.com/AI4SAI/sai-hpc-software/actions/runs/34124103777) | 1159976（repack/verify） | success |
| `v100` | [34124108251](https://github.com/AI4SAI/sai-hpc-software/actions/runs/34124108251) | 1159974 | success |

V100 运行是完整 CUDA 编译、安装、SIF 封装及运行时验证。CPU 运行的首次编译已成功；随后使用同一编译 overlay 完成了修复后的 repack 验收，因此没有重复消耗一次完整 CPU 编译。

上游源码为 `fb9ce1da4114818b5ba4a1a899996d5432722035`（ABACUS `v3.11.0-beta9`）。缓存命中时日志显示 `zero source upload`；缓存未命中时使用 gzip、8 分片和校验和验证的 bundle 接收流程。

## SAI 上的成品

```text
/home/stardust/sai-hpc-software/containers/software/abacus/
└── develop-fb9ce1da4114/
    ├── cpu-misc/34124103777-1-cpu-misc-develop-fb9ce1da4114.sif  (~7.1 MiB)
    └── v100/34124108251-1-v100-develop-fb9ce1da4114.sif          (~9.1 MiB)
```

每个 SIF 旁有同名 `.json` sidecar，记录源码 SHA、目标、控制器快照和 SIF SHA-256。镜像内安装前缀保持绝对路径：

```text
/opt/software/abacus/develop-fb9ce1da4114/<cpu-misc|v100>/
```

镜像执行了 `abacus --info`、`ldd` 未解析依赖检查和只读写入测试；普通用户无需 `--fakeroot` 即可运行 CPU SIF。运行时环境记录于 `share/sai/runtime-env.sh`，避免在只读容器中重新初始化 Lmod。

## 未通过/未覆盖

SAI 的 `8A100M40` 和 `8A100M80` 当前各只有一个节点且状态为 `UNKNOWN/NOT_RESPONDING`（`unk*`），因此 A100 作业无法进行有意义的编译验收；控制器保留该目标供管理员恢复节点后手动 dispatch，日常矩阵暂不包含 A100。PW SCF 是小型科学算例，不替代完整的 ABACUS 回归测试集。

## 复核命令

```bash
gh run view 34124103777 --repo AI4SAI/sai-hpc-software --json status,conclusion,url
gh run view 34124108251 --repo AI4SAI/sai-hpc-software --json status,conclusion,url
ssh SAI-stardust 'squeue -h -u stardust'
ssh SAI-stardust 'cat /home/stardust/sai-hpc-software/runs/34124108251-1-v100-develop-fb9ce1da4114/artifact.path'
```
