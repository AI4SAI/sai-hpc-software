# sai-hpc-software 交接文档

更新时间：2026-09-08（Asia/Shanghai）

本文件记录当前项目的需求背景、架构、真实验收、故障修复和后续维护方式。文档不包含 SSH 私钥、Actions secret 内容或其他凭据。

## 1. 用户目标

目标是在同一组织的公开仓库 AI4SAI/sai-hpc-software 中维护多种 HPC 软件的自动安装流程，当前以 ABACUS 为首个软件样例。

核心约束：

- GitHub Actions 通过 SSH 进入 SAI，在用户家目录下运行。
- 使用真实 Slurm 分区、Apptainer 1.4.4 和站点预置 module 环境。
- 外部源码、编译树、安装树和编译临时状态都在容器文件系统/文件型 overlay 中。
- 最终交付物是单个只读 SIF，不能把容器内安装树展开成宿主 inode 树。
- 安装前缀保持绝对路径 /opt/software/...，兼容硬编码路径和 module 逻辑。
- 管理员以后可手工把 SIF 中的安装结构部署到宿主 /opt；仓库不负责该管理动作。
- 计算时由宿主 Open MPI/Slurm 在容器外启动多节点 rank，每个 rank 再执行 apptainer exec --nv 进入同一个 SIF。
- 4V100 和 16V100 分开编译：前者 AVX-512/znver4，后者 AVX2/znver3；两者 CUDA 架构均为 V100 sm70。
- 项目源码、缓存、构建状态和 runtime 文件放在 /home/stardust/sai-hpc-software；项目流程不使用宿主 /tmp。
- 源码采用 gzip bundle、8 分片、校验和、远端 Git bundle cache；cache hit 时跳过源码上传并复用远端对象。
- source SHA 由 branch、latest release、latest pre-release 动态解析，不在 workflow 中硬编码。

## 2. 仓库与当前版本

- 本地：/mnt/g/work/agent/sai-hpc-software
- 远端：https://github.com/AI4SAI/sai-hpc-software
- 默认分支：main
- 功能基线：518185c；交接文档提交为 d56c133、c994406；当前 main 还包含这些文档提交
- 最新完整绿色 Actions：34189103202
- Actions URL：https://github.com/AI4SAI/sai-hpc-software/actions/runs/34189103202
- 本轮上游：ABACUS develop，SHA 149723287702677dc159c5e96fb98cbc3482f445
- 本轮版本目录：develop-149723287702

关键提交：

| 提交 | 内容 |
| --- | --- |
| a5790e1 | 精确 V100 profile、znver4/znver3、宿主 MPI runtime 骨架 |
| ca12a5e | rank binding 和 Slurm/MPI/CUDA 环境传递 |
| 59ee7fd | runtime 固定本次 build artifact，消除 current.sif 并发竞态 |
| 8f42957 | 固定双节点、每节点一 GPU，并验证 avx512/avx2 MPI |
| a4ba4d0 | 修复 set -u 下 USER/LOGNAME 初始化顺序 |
| 3f65611 | runtime 固定本次 controller 快照中的 launcher |
| bb5740a | 初始化 LD_LIBRARY_PATH，显式检查 Apptainer |
| 3065a55 | 同绝对路径 bind 宿主 PMIx session，解决 PMIx shmem2 |
| 85b6a6e | 退出时删除 per-job runtime 空父目录 |
| 518185c | 完整验收记录，monitor 对消失的 squeue job 使用 sacct |
| d56c133 | 保存本交接文档 |
| c994406 | 修正交接文档 revision 元数据 |

## 3. Actions、SSH 和 secrets

工作流是 .github/workflows/build.yml：

1. validate：Python 单元测试、Python 编译、shell 语法。
2. resolve-source：调用 controller/resolve_source.py 解析 branch/tag/release/pre-release。
3. build：矩阵调用 controller/ci.py，上传可信 controller，接收远端 Git bundle，提交/监控 Slurm build，发布 SIF，并对精确 V100 target 自动提交双节点 runtime acceptance。

push/PR 默认只 validate；实际访问 SAI 的 build 使用 workflow_dispatch 或 schedule，并受 hpc Environment 保护。

Secret 名称：

- REMOTE_SSH_PRIVATE_KEY
- REMOTE_USER

私钥只临时写入 Actions runner 的 $RUNNER_TEMP/ssh/key，权限 0600，step 后删除；本项目没有读取、打印或提交私钥内容。公开 known_hosts 文件位于 .ci/slurm/known_hosts。

手动 dispatch：

~~~bash
gh workflow run build.yml \
  --repo AI4SAI/sai-hpc-software --ref main \
  -f source_ref=develop \
  -f targets=4v100-avx512,16v100-avx2
gh run list --repo AI4SAI/sai-hpc-software \
  --workflow build.yml --event workflow_dispatch
~~~

## 4. 远端目录布局

远端根目录固定为 /home/stardust/sai-hpc-software：

~~~text
cache/repositories/abacus/        bare Git cache
containers/base/minimal-v1.sif    管理员预置最小构建容器
containers/software/abacus/<version>/<target>/
  <run-id>.sif                     单文件只读成品
  <run-id>.json                    SHA、来源、target、runtime sidecar
  current.sif -> <run-id>.sif
controller/<controller-sha>/<run-id>/ 可信 controller 快照
modulefiles/apps/abacus/<version>     用户 modulefile
runs/<build-run-id>/
  input/ results/ runtime/ apptainer-cache/ work.ext3
runtime-tests/<runtime-run-id>/        runtime 输入、日志、rank traces
runtime/jobs/<slurm-job-id>/           per-rank Apptainer/PMIx runtime
~~~

构建成功后 work.ext3 会删除；SIF、sidecar、cache 和日志保留。current.sif 是 catalog 内符号链接，实际 SIF 是普通文件、权限 0444。runtime/jobs 的 per-rank 内容在退出时清理，空父目录也会尝试删除。

## 5. 构建隔离模型

controller/remote_controller.py::container_command() 的构建策略：

- apptainer exec --fakeroot --cleanenv --containall --network none；
- 最小 base SIF；
- /usr、/lib、/lib64、/opt/devtools、/opt/modules 只读 bind；
- source bare repository 只读 bind 到容器 /input/repository；
- controller 只读 bind 到 /control；
- work.ext3 作为 overlay；
- TMPDIR 使用 overlay 内 /workspace/tmp；
- 容器内编译、安装并导出 SquashFS，宿主只负责组装最终 SIF。

ABACUS 安装前缀为：

~~~text
/opt/software/abacus/<version>/<target>/
~~~

SIF 内的 share/sai/ 保存 source SHA、target、hardware、resolved environment、module list、CMakeCache、runtime-env 和 smoke-case 输入/伪势文件。

## 6. 精确 target profile

| target | Slurm partition | CPU/ISA | MPI/BLAS auto | CUDA |
| --- | --- | --- | --- | --- |
| dsprhbm | DSPRHBM | Xeon Platinum 9470，Sapphire Rapids HBM，x86-64-v4，AVX-512 | auto | 无（module gcc/13.3.0） |
| 4v100-avx512 | 4V100 | Ryzen 9 9950X3D，znver4，AVX-512/VNNI | ...-avx512 | V100 sm70 |
| 16v100-avx2 | 16V100 | Threadripper PRO 5995WX，znver3，无 AVX-512 | ...-avx2 | V100 sm70 |
| a100 | 8A100M40 | 预留 | auto | sm80 |

精确编译选项：

~~~text
4V100:
  -DENABLE_NATIVE_OPTIMIZATION=OFF
  -DCMAKE_CXX_FLAGS="-march=znver4 -mtune=znver4"
  -DCMAKE_CUDA_ARCHITECTURES=70

16V100:
  -DENABLE_NATIVE_OPTIMIZATION=OFF
  -DCMAKE_CXX_FLAGS="-march=znver3 -mtune=znver3"
  -DCMAKE_CUDA_ARCHITECTURES=70
~~~

*-auto module 必须在实际 compute node 上加载，不能在 login node 预先解析。

## 7. Runtime 与 module 模型

运行拓扑：

~~~text
Slurm allocation
  -> compute-node module load
  -> host Open MPI/PRRTE
  -> trusted abacus launcher per rank
  -> apptainer exec --nv
  -> ABACUS binary inside read-only SIF
~~~

生成 modulefile 位于 /home/stardust/sai-hpc-software/modulefiles/apps/abacus/<version>，会：

- prepend-path MODULEPATH /opt/modules/modulefiles/devtools；
- 加载 apptainer/1.4.4；
- 加载 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto；
- 设置 SAI_SOFTWARE_ROOT、SAI_ABACUS_VERSION；
- 将受信 launcher 放入 PATH。

人工计算示例（必须已在 Slurm allocation 内）：

~~~bash
source /etc/profile.d/lmod.sh
module use /home/stardust/sai-hpc-software/modulefiles/apps
module load abacus/develop-149723287702
cd /home/stardust/sai-hpc-software/my-calculation
source /opt/sai_config/mps_mapping.d/$SLURM_JOB_PARTITION.bash
export TMPDIR="$SAI_SOFTWARE_ROOT/runtime/jobs/$SLURM_JOB_ID/mpi"
mkdir -p "$TMPDIR"
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher
export PRTE_MCA_plm_slurm_args=--external-launcher
mpirun -np "$SLURM_NTASKS" --map-by "$MAP_OPT" abacus
~~~

自动 acceptance 固定两节点、每节点一 GPU、每节点一个 rank。站点 MPS mapping 在多 rank/GPU 时会使用宿主 /tmp，因此当前 acceptance 刻意不走 MPS 路径。

launcher 的关键约束：

- 4V100 -> 4v100-avx512；16V100 -> 16v100-avx2；未知分区拒绝；
- 只接受本次 request 固定的 catalog 内普通 SIF；
- --nv 暴露 Slurm 分配的 NVIDIA 设备和 host driver libraries；
- /usr、/lib、/lib64、/opt/devtools 只读 bind；计算目录可写；
- --cleanenv 下显式传递 SLURM、OMPI、OPAL、PMIX、PMI、PRTE、UCX、NCCL、CUDA 等环境族；
- Apptainer 自身使用 per-rank APPTAINER_TMPDIR；
- host MPI/PRTE 使用的 TMPDIR 在项目 runtime-tests/.../mpi-runtime，并按相同绝对路径 bind 到容器；这是解决 PMIX_ERR_FILE_OPEN_FAILURE 的关键；
- 不使用 --fakeroot、--network none、--containall 或整棵 /opt 的 writable bind；
- 项目流程不依赖宿主 /tmp。

## 8. 最新完整真实验收

Actions run 34189103202 最终 completed/success。validate、resolve-source、两个 build matrix job 和自动 runtime 均成功；日志出现 CACHE_HIT zero source upload、BUILD_AND_ARTIFACT_VERIFIED、MULTINODE_CONTAINER_MPI_VERIFIED。

| target | build job/node | SIF | SHA-256 | runtime job/nodes | energy (eV) |
| --- | --- | --- | --- | --- | --- |
| 4v100-avx512 | 1178480 / 4v100n23 | /home/stardust/sai-hpc-software/containers/software/abacus/develop-149723287702/4v100-avx512/34189103202-1-4v100-avx512-develop-149723287702.sif | a362054206c865f80fd1e0dec7522aaf16b37d9361ea6a24d3074d8cc3c4995e | 1178916 / 4v100n15,4v100n16 | -4869.7470519499920556 |
| 16v100-avx2 | 1178478 / 16v100n02 | /home/stardust/sai-hpc-software/containers/software/abacus/develop-149723287702/16v100-avx2/34189103202-1-16v100-avx2-develop-149723287702.sif | 61ff6383cbbd4a6bcff25cf00475ccbe48a1d3d6cb0d4deb19226015fd19bc70 | 1178918 / 16v100n81,16v100n82 | -4869.7470519499847796 |

每个 runtime 都满足：

- 两个 rank trace、两个 distinct hostname；
- 每个 rank CUDA_VISIBLE_DEVICES=0；
- 4V100 OPAL_PREFIX 为 ...cuda12-avx512；
- 16V100 OPAL_PREFIX 为 ...cuda12-avx2；
- 两个 rank 指向同一个固定 SIF；
- GPU ... (x2)、SCF converged、能量在 1 eV 容差内；
- MULTINODE_CONTAINER_MPI_VERIFIED；
- 无 PMIx/MPI_Init abort；
- sidecar verified:true 且 multinode_runtime.verified:true。

最后的 cleanup 验证 job 为 1178966（4V100），也成功完成；它验证了最新 launcher 退出后可以清理 per-job runtime 目录。当前 squeue 无残留作业。

## 9. 历史故障与修复

1. 1177678/1177823：旧脚本将 USER 与 LOGNAME=$USER 放在同一条 export，set -u + --export=NIL 导致 USER unbound。已拆成两行。
2. 1178014/1178015：Lmod 在 set -u 下读取未定义 LD_LIBRARY_PATH，且 Apptainer 不在 PATH。已初始化 LD_LIBRARY_PATH/LD_PRELOAD，并显式检查 command -v apptainer。
3. 1178128/1178129：容器内 MPI_Init 报 PMIX_ERR_FILE_OPEN_FAILURE。原因是 host PRTE session 在共享家目录，而容器只有私有 /runtime。已 bind 相同绝对 host MPI TMPDIR；1178280/1178282 因此通过。
4. 并发构建竞态：runtime 原来从全局 current.sif 取镜像，可能验收 B 却给 A 写 sidecar。现在通过 build-run-id 固定 artifact.path、sidecar 和 SHA256。
5. 资源/ISA 误验收：原来允许 3/4 节点或每节点 2 GPU。现在 acceptance 固定 2 节点 × 1 GPU/节点，并检查 target-specific AVX MPI 路径。
6. monitor 边界：完成 job 可能已经离开 squeue，squeue 会 exit 1；当前 monitor 忽略该查询错误并使用 sacct 判断终态。

## 10. 手工查看和验收

加载已发布版本：

~~~bash
ssh SAI-stardust
source /etc/profile.d/lmod.sh
module use /home/stardust/sai-hpc-software/modulefiles/apps
module load abacus/develop-149723287702
command -v abacus
readlink -f "$SAI_SOFTWARE_ROOT/containers/software/abacus/$SAI_ABACUS_VERSION/4v100-avx512/current.sif"
~~~

只读查看 SIF 内文件，不展开到宿主：

~~~bash
image=/home/stardust/sai-hpc-software/containers/software/abacus/develop-149723287702/4v100-avx512/34189103202-1-4v100-avx512-develop-149723287702.sif
module load apptainer/1.4.4
apptainer exec --cleanenv --containall --no-home \
  --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro \
  "$image" /usr/bin/find /opt/software -maxdepth 6 -type f
~~~

查看 Actions 和远端记录：

~~~bash
gh run view 34189103202 --repo AI4SAI/sai-hpc-software \
  --json status,conclusion,jobs,url
ssh SAI-stardust 'squeue -h -u stardust'
ssh SAI-stardust 'cat /home/stardust/sai-hpc-software/runs/34189103202-1-4v100-avx512-develop-149723287702/artifact.path'
~~~

## 11. 本地验证

~~~bash
python3 -m unittest discover -s tests -v
python3 -m py_compile controller/*.py
bash -n controller/*.sh
git diff --check
~~~

截至交接时：18 项测试通过，Python 编译通过，shell 语法通过；Actions validate 也通过。

## 12. 已知边界和后续开发

- A100 目标仍预留；8A100M40/8A100M80 曾为 UNKNOWN/NOT_RESPONDING，未纳入本轮真实 acceptance。
- 当前科学 smoke 是 tests/11_PW_GPU/scf_cg，验证 CUDA-aware MPI、两节点、两 GPU、SIF 进入和 SCF 数值；不覆盖 ELPA 工作负载，也不证明 ABACUS NCCL collective。
- acceptance 有意限制每节点一 rank/一 GPU，避免站点 MPS mapping 使用宿主 /tmp。若需多 rank/GPU，先把 MPS pipe/log 路径迁到项目家目录并重新验证。
- 当前完整可信 recipe 只有 ABACUS。新增软件需增加受信配方、安装元数据、smoke case、module 生成逻辑和 tests，不能让 workflow 直接执行未经审计外部脚本。
- 新 target 必须同步更新 TARGETS、profile、编译 flags、module/runtime 映射、tracking 和 tests，并在实际 compute node 验证 auto module。
- 不要把 source SHA 固定在 workflow；继续通过 resolve_source.py 解析 branch/release/pre-release，并使用远端 bundle cache。

## 13. 后续第一步建议

1. 先阅读 README.md、controller/ci.py、controller/software_controller.py、controller/runtime_controller.py、controller/abacus_runtime.sh 和 docs/acceptance-2026-09.md。
2. 用 gh run view 34189103202 和 SAI 上两个 sidecar 重现本轮绿色证据。
3. 新增软件时复制“受信 recipe + container entry + module + runtime smoke + tests”的结构，不要复制旧的未经 PMIx bind 验证的容器脚本。
4. 任何 runtime 失败先保留 Slurm log、rank traces 和 sidecar，再定位原因；不要直接删除整个 runs/runtime-tests 目录。
