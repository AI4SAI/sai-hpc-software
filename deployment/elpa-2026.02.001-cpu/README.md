# ELPA 2026.02.001：DSPRHBM 纯 CPU 版和分区 module

2026-10-19：CPU 构建/验收任务 `1848059` 已 `COMPLETED 0:0`，用时 2 分 4 秒，
原始显式 C 接口的 24 次求解全部通过。四分区 module 任务 `1848160`–`1848163`
也全部通过。管理员补充包位于 `/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-cpu`，
12.81 MiB 的 CPU 载荷已通过预检；尚待管理员安装。
[验收证据](https://github.com/AI4SAI/sai-hpc-software/blob/fix/workflow-closure/docs/evidence/elpa-cpu-20261019/README.md)。

同日后续：按用户要求沿用 `-auto` 名称，替换候选的四分区任务 `1861785`–`1861788`
全部 `COMPLETED 0:0`，包含实际数值求解和旧 module 升级后的卸载检查。
[auto 升级验收记录](../../docs/evidence/elpa-auto-20261019/README.md)。

为无 NVIDIA 驱动的 DSPRHBM 新增不含 GPU 支持的 ELPA，保留原 GCC 13.3.0、
OpenMPI 5.0.10、OpenBLAS 0.3.32、ScaLAPACK 2.2.3 和 OpenMP 配置。
CPU 编译参数为 `-march=x86-64-v4 -mtune=sapphirerapids`。
构建在 DSPRHBM 上的 Apptainer 容器内执行，源码、对象和临时文件均位于家目录。
容器内和未来物理安装使用同一前缀：

```text
/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu
```

关闭 NVIDIA/AMD/SYCL GPU 内核、CUDA-aware MPI、cuSOLVER 和 CUB 后，
`elpa_eigenvectors_double`、`elpa_eigenvectors_double_complex` 会直接调用 CPU 实现，
不再进入 `is_device_ptr` / `cudaPointerGetAttributes`。无需修改调用程序或安装 CUDA 驱动。
CPU 库和 pkg-config 信息不引入 CUDA 库依赖。站点 MPI 本身仍沿用既有配置和实际 HPCX 路径。

沿用并升级 module：`elpa/2026.02.001-2603-gnu-auto`。
它要求在实际 Slurm 任务或交互分配中加载，并验证节点 ISA 与站点 MPI/BLAS 匹配。
根据 `SLURM_JOB_PARTITION` 选择已编译好的库，不会在加载时编译。
卸载时使用保存的分区身份；升级前已经加载旧 GPU module 的会话也可以正常卸载。

| 分区 | ELPA 路径后缀 | 产物 |
| --- | --- | --- |
| 16V100 | `-gnu-avx2/nvidia` | 已验收的 GPU 版 |
| 8V100V0 | `-gnu-avx2/nvidia` | 已验收的 GPU 版 |
| 4V100 | `-gnu-avx512/nvidia` | 已验收的 GPU 版 |
| DSPRHBM | `-gnu-avx512/cpu` | 新增纯 CPU 版 |

管理员安装后，原有 `module load elpa/2026.02.001-2603-gnu-auto` 命令直接使用上述分区映射。
不另行安装 `-partition` module。CP2K 已验收的私有 ELPA 不受此操作影响。

## 管理员部署

部署包从通过验收的最终 CPU SIF 提取；新增 `cpu/` 后，备份并原子替换现有 `-auto` module。
预检还会核对物理 GPU 安装与已验收的清单一致，并只允许替换清单中固定的旧 module。
备份保存在 module 同目录的隐藏文件 `.2026.02.001-2603-gnu-auto.before-<旧SHA256>`，
不会作为额外版本显示。重复安装会验证并保留已安装的新版本。
不复制系统依赖或构建缓存。

```bash
cd /home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-cpu
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

管理员无需执行容器。安装完成后在计算节点加载：

```bash
module load elpa/2026.02.001-2603-gnu-auto
```

`--verify` 仅核对物理文件。物理 CPU 路径上的数值运行验收在管理员安装后进行。
容器阶段验收使用原始显式 C 接口、实数/复数矩阵、ELPA1/ELPA2、单/双 MPI 进程、
两次重复求解和 ELPA2 非阻塞选项，并编译运行 Fortran 初始化调用程序。
分区 module 另检查加载/卸载和实际 MPI 进程的库加载路径。
需要切换版本的已有会话应在对应 Slurm 分配内先 `module unload` 再 `module load`。

## 重现构建

在集群开发 checkout 执行：

```bash
bash deployment/elpa-2026.02.001/submit-cpu.sh
bash deployment/elpa-2026.02.001-cpu/submit-module-checks.sh <通过验收的CPU运行目录>
```

GPU 分区的 module 检查使用已安装的物理库，CPU 分区使用最终 SIF 中的新库。
对应 Slurm 状态保存为 module 结果目录的 `accounting.txt` 后，使用
`prepare-release.py --run <CPU运行目录> --module-checks <module结果目录> --output <新部署目录>`
核对数值结果、module 校验和及实际库加载证据，再提取管理员部署包。
同样参数使用 `--verify-only` 替代 `--output`，可只读复核最终 SIF、Slurm 状态和原始日志。
可添加 `--payload-from <此前的CPU部署包>` 复用已提取的相同 SIF 载荷，仍会核对其来源、
文件清单和内容。旧 GPU 部署包保留最初 module 的历史验收记录；升级后的物理文件使用
本补充包的 `install.sh --verify` 验证。
