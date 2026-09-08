# 真实集群验收记录（2026-09）

本记录对应公开仓库 `AI4SAI/sai-hpc-software` 的真实 GitHub Actions → SSH → Slurm → Apptainer 流程。运行均使用 SAI 上用户家目录中的项目根目录；源码、编译树、安装树和 SquashFS 组装过程都在单个 ext3 overlay 内完成。

## 已通过

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

SAI 的 `8A100M40` 和 `8A100M80` 当前各只有一个节点且状态为 `UNKNOWN/NOT_RESPONDING`（`unk*`），因此 A100 作业无法进行有意义的编译验收；控制器保留该目标供管理员恢复节点后手动 dispatch，日常矩阵暂不包含 A100。当前验收也未替代真实科学输入的正确性测试。

## 复核命令

```bash
gh run view 34124103777 --repo AI4SAI/sai-hpc-software --json status,conclusion,url
gh run view 34124108251 --repo AI4SAI/sai-hpc-software --json status,conclusion,url
ssh SAI-stardust 'squeue -h -u stardust'
ssh SAI-stardust 'cat /home/stardust/sai-hpc-software/runs/34124108251-1-v100-develop-fb9ce1da4114/artifact.path'
```

