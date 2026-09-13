# 真实集群验收记录（2026-09）

本记录对应公开仓库 `AI4SAI/sai-hpc-software` 的真实 GitHub Actions → SSH → Slurm → Apptainer 流程。运行均使用 SAI 上用户家目录中的项目根目录；源码、编译树、安装树和 SquashFS 组装过程都在单个 ext3 overlay 内完成。

## 自动验收修复与现场复核（2026-09-11）

最新基线 `57076fb` 的定时运行
[`34573969365`](https://github.com/AI4SAI/sai-hpc-software/actions/runs/34573969365)
不能作为全矩阵成功记录：DSPRHBM release 的编译作业 `1263724` 成功，但
`runtime_controller.py submit` 的 CLI choices 漏了 `dsprhbm`，验收没有提交。
develop CPU 和两个 release GPU 都命中旧产物；只有两个 develop GPU 重新编译并完成双节点 PW 验收。

两个 develop GPU SIF 的现场 `--info`、CMakeCache、`ldd` 均已核实：
加载 `nvmplibs/26.7-tmp`，CUSOLVERMP/CUBLASMP/NCCL_PARALLEL_DEVICE 开关均为 ON，
cuSOLVERMp/cuBLASMp 解析到 `/opt/devtools/nvidia/mp_libs/lib`，NCCL 解析到站点
`nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib`，无未解析依赖。
抽查 release AVX-512 的 9 月 8 日旧包，其 CUSOLVERMP/NCCL 开关仍为 OFF。

修复 CPU 入口后，在不改旧 SIF 的情况下，用独立可信快照重新验收：

| 运行 | Slurm | 节点 / rank | 结果 |
| --- | --- | --- | --- |
| `acceptance-fix-cpu-20260911-v1` | `1270181` | `d9470n01,d9470n04` / 16 | COMPLETED 0:0，PW SCF 收敛 |
| `acceptance-fix-cpu-20260911-v2` | `1270237` | `d9470n01,d9470n04` / 16 | 加强版独立证据校验通过 |

v2 最终能量为 `-4869.747051960287 eV`，误差小于 `1e-5 eV`。
`runtime-tests/acceptance-fix-cpu-20260911-v2/results/evidence.json` 固定了镜像、
脚本、原始 rank traces、SCF/Slurm 日志的哈希；CPU 每节点 8 ranks、每 rank 2 threads，
不要求 CUDA_VISIBLE_DEVICES 非空。这是旧包的手工入口回归，不冒充新配方的完整 Actions 发布。

新契约将候选与发布分开，缓存核对配方指纹、构建状态及所需验收的原始科学结果。
GPU 另要求实际 Si2 LCAO cuSOLVERMp 求解及 GaAs BPCG 的跨 rank NCCL collective；
仅加载库、初始化 NCCL、查询 bufferSize 或 Slurm 返回零均不算专项通过。

### 修复分支的完整自动验收

配方提交 `0d71314212044d66fcefab8de1a638daec362760` 的
[`34594725615`](https://github.com/AI4SAI/sai-hpc-software/actions/runs/34594725615)
已全部成功，上游源码为 `9cbcc0b55deba41428f4fd4f9964bf099fdfb589`。
三个目标均重新编译，经科学验收后才发布；不是旧配方缓存的绿灯。

| 目标 | 构建 Slurm | 双节点 PW Slurm / 最终能量 (eV) | cuSOLVERMp / NCCL 专项 Slurm |
| --- | --- | --- | --- |
| `dsprhbm` | `1270401` | `1270633` / `-4869.7470519602866261` | CPU 不要求 |
| `4v100-avx512` | `1270394` | `1270826` / `-4869.7470519499865986` | `1270827` |
| `16v100-avx2` | `1270395` | `1270835` / `-4869.7470519499902366` | `1270837` |

GPU 专项分别在 `4v100n15,4v100n16`、`16v100n77,16v100n78` 执行。
Si2 LCAO 日志记录实际 `cusolverMpSygvd` 调用，能量分别为
`-196.62217237013218`、`-196.6221723701322 eV`。
GaAs BPCG 日志记录真实 `AllGather: opCount ... [nranks=2]`，能量分别为
`-4869.747051979221`、`-4869.7470519792205 eV`。
这里验证的是 NCCL collective，不将它误称为实际执行了 AllReduce。

已发布 SIF 位于 `containers/software/abacus/develop-9cbcc0b55deb/<target>/`：

| 目标 | SIF SHA-256 |
| --- | --- |
| `dsprhbm` | `f71527be7655195423a25d2de5c6497e443652037961b7484e7dbb71e1a114bc` |
| `4v100-avx512` | `e9f2b2e6a3ccfc181b96c25038a769b247c1bb2d1333172d318f0a05b8446f44` |
| `16v100-avx2` | `42b81d72e75fb535b17ac07d85bf28bef358a53b404ae4e10c42bcb84c8521ef` |

原始证据保存在 `runtime-tests/34594725615-1-<target>-develop-9cbcc0b55deb-<multinode|gpu-features>/`。
这是 CPU/MPI/cuSOLVERMp/NCCL 发布门槛的通过记录；尚不是与预装软件的完整功能、
性能对比。全功能和同节点重复测速仍在独立实验分支推进，未并入 `main`。

### 四分区原生编译与 8V100V0

`d25c080` 改为在各目标分区使用 `-march=native -mtune=native`，并将软件 CPU 指令集
与站点依赖库 ISA 分开记录。不能因为 8V100V0 支持 AVX-512 就强制加载站点 AVX-512 库。

| 分区 | 软件原生架构 | 站点 MPI / BLAS 变体 |
| --- | --- | --- |
| DSPRHBM | 节点原生，AVX-512 | AVX-512 |
| 4V100 | znver4 | AVX-512 |
| 16V100 | znver3 | AVX2 |
| 8V100V0 | skylake-avx512 | AVX2 |

8V100V0 探测作业 `1270675` 在 `8v100v0n01` 完成：Xeon Gold 6146、
V100-SXM2-32GB / sm70；有 AVX-512、无 VNNI。GCC 原生选项为 skylake-avx512，
站点 auto 模块选择 `openmpi-5.0.10-nvhpc263-gnu-cuda12-avx2` 和
`saiblas/2603-gnu-avx2`，并复用系统 ELPA 2026.02.001。
[`34599259331`](https://github.com/AI4SAI/sai-hpc-software/actions/runs/34599259331)
已全部成功：原生构建 `1270988`，双节点 PW `1272960`，GPU 专项 `1272980`，
三个作业均 `COMPLETED 0:0`。运行节点为 `8v100v0n01,8v100v0n02`。
PW 能量 `-4869.7470519499747752 eV`，Si2 cuSOLVERMp 能量
`-196.6221723701321 eV`，GaAs BPCG/NCCL 能量 `-4869.747051979224 eV`。
原始日志记录实际 `cusolverMpSygvd` 和跨节点 `AllGather ... [nranks=2]`。
SIF SHA-256 为 `d7637612daee98484f47748e0642969f89895f2f802e9b35763089fcbbf028b0`。
这证明第四分区的原生构建和基础科学/GPU 门槛，仍不等于独立全功能分支的性能验收。

### module 分区隔离

发现同版本 module 由最后完成的分区覆盖其 launcher PATH，CP2K 还可能覆盖 CPU/GPU
依赖选择。修复为共用 selector + 每分区 immutable `<run>.module`，后者同时固定已经
验收的 SIF 和 launcher；缓存重新校验 selector、fragment 和 launcher。
同 target 发布串行加锁，避免两个 current 指针的并发交错；已加载 module 保留自己的
run token，更新其他分区或同分区的新版本不改变其镜像/launcher，卸载仍使用原 token。

真实 Lmod 探针 `1273310` 在 DSPRHBM `d9470n01` 完成 `0:0`，验证跨分区发布不改变
CPU 选择、更新后卸载旧 pair、重新加载新 pair 和无执行的 `module show`。
日志为 `runs/module-publication-probe-20260911-v2/results/slurm-1273310.log`。
探针只使用 `experimental/module-publication-20260911-v2` 中明确标记的 module 测试元数据，
没有将它们冒充科学 SIF，也没有更改生产 module。首次探针 `1273208` 在 reload 时因站点
Lmod shell wrapper 不兼容 `set -u` 而失败；第二次在 module 调用期间关闭 nounset 后通过。
这项新发布逻辑未被前述 `d25c080` 的旧构建运行使用，需要后续新契约发布落地。

## CP2K 实际进展复核（2026-09-11）

CP2K 确有集群手工编译成果，不能因 Actions 只有静态检查绿灯就断言未编译：

| 作业 | 现场状态 | 含义 |
| --- | --- | --- |
| `1216695` | FAILED 127:0 | 已完成编译安装，随后因 `libcp2k.so.2026.2` 搜索路径失败 |
| `1226525` | COMPLETED 0:0 | 原 overlay 上版本/链接验证及手工 repack 完成 |
| `1227313` | COMPLETED 0:0 | TBLITE/DFT-D4 依赖归档成功，不是仍待编译 |

相关日志位于 `runs/cp2k-full-1216695.log`、`runs/cp2k-repack-1226525.log`、
`runs/cp2k-tblite-1227313.log`。历史 GPU 作业显示 CP2K `2026.2 Development`、
revision `6d276e9`，flags 包含 `omp parallel scalapack dbcsr_acc openblas offload_cuda
cusolvermp cusolvermp_nccl`。

旧 SIF 为 `runs/cp2k-repack-1216695/cp2k-latest-v100.sif`（445530112 bytes）。
现场 SHA-256 为 `f2c9b32f9314c96f4c840d3c5027227235019c749eafce6fd541bd47adf7badf`。
它不是正式发布：`containers/software/cp2k` 没有已发布目录，且最终镜像有明确缺陷：

- 实际安装路径是 `/opt/software/latest-v100`，而编译前缀应为 `/opt/software/cp2k/latest-v100`。
  原因是 export 复制前未创建目标 `opt/software`，导致目录被重命名。当前 CP2K recipe 仍需修复此处。
- 缺少 `share/sai/runtime-env.sh`、source-sha 和 CMakeCache。旧 repack 校验的是原 overlay，
  并没有验证生成后的 SIF，因而漏过了路径漂移。
- 调整诊断环境后，实物 `ldd` 可解析 GPU 库；登录节点执行版本会触发 CUDA 初始化错误，
  不可将其当成 GPU 计算验收。未发现该 SIF 的科学算例验收。

没有将这个有缺陷的旧 SIF 冒充已发布版本或修改它。修复、四分区原生构建和科学/性能
验收在独立分支 `feat/cp2k-parity-benchmarks` 进行，静态测试不等于实际新成品通过。
CPU 预装对照为 `cp2k/2025.1-cpu-auto`；GPU 对照为 `cp2k/2026.1-cuda12.9-sm70-auto`。
用户已明确允许 CP2K 2026 不含上游移除的 QUIP 接口，不额外保留 2025 兼容构建；
这不豁免其他功能的对照和验收。

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

9 月 8 日探测时，SAI 的 `8A100M40` 和 `8A100M80` 各只有一个节点且状态为
`UNKNOWN/NOT_RESPONDING`（`unk*`）。当前 ABACUS 发布策略在提交前拒绝未注册运行验收的
A100 目标，四分区日常矩阵不包含 A100。PW SCF 是小型科学算例，不替代完整回归或性能对照。

## 复核命令

```bash
gh run view 34124103777 --repo AI4SAI/sai-hpc-software --json status,conclusion,url
gh run view 34124108251 --repo AI4SAI/sai-hpc-software --json status,conclusion,url
ssh SAI-stardust 'squeue -h -u stardust'
ssh SAI-stardust 'cat /home/stardust/sai-hpc-software/runs/34124108251-1-v100-develop-fb9ce1da4114/artifact.path'
```
