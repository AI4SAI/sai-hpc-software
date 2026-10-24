# 已验收开发版 SIF：Lmod 与宿主 MPI

这份固定快照展示本轮已验收的 17 份 SIF。日期 `20261024` 是交付快照日期，
提交号表示源码版本；各分区的 recipe、镜像 SHA256、安装前缀和验收记录保存在
`snapshot.json`。`prepare.py` 可以从仓库中的原始验收记录重建清单。

| Lmod 名称 | 命令 | 分区 |
| --- | --- | --- |
| `abacus-sif/develop-20261024-g370ba685e670` | `abacus` | 4V100、16V100、8V100V0、DSPRHBM |
| `cp2k-sif/master-20261024-gcfe5e5d87aaf` | `cp2k.psmp`、`cp2k` | 同上 |
| `lammps-sif/develop-20261024-ga0e2bd6efb3d` | `lmp` | 三个 V100 分区 |
| `deepmd-kit-sif/master-20261024-g558f56962775` | `dp`、`deepmd-python` | 三个 V100 分区 |
| `gpumd-sif/master-20261024-g2c93346fce44` | `gpumd`、`nep`、`gnep` | 三个 V100 分区 |

登录节点可浏览、查看帮助；在实际 Slurm allocation 内加载，自动按
`SLURM_JOB_PARTITION` 选择镜像和匹配的 MPI。未知分区会明确报错。
卸载使用加载时保存的分区，退出 allocation 后也能正确卸载。
`*-sif` 与已有物理安装模块分开命名。加载 SIF 模块会检查已加载的同程序物理
模块；切换入口时先卸载前一模块，或执行 `module purge`。

项目内已验证的入口：

```bash
module use /org/org-open-sai/proj-sai-hpc-software/sif/20261024-r4/modulefiles
module --ignore_cache avail cp2k-sif
module help cp2k-sif/master-20261024-gcfe5e5d87aaf
```

项目目录有访问 ACL；其他用户应使用下文的公共安装。新的 daily build 仍按
原工作流进行。该固定快照不会自行改变镜像；后续快照应重新核对整套分区验收，
再增加版本化目录和 module。

## 用户作业

以下示例采用本轮验证的两节点、每节点两个 rank/一个 GPU 配置。
实际大体系可按算法调整 rank、GPU 和 OpenMP 数量；GPU 作业不显式指定
`--cpus-per-task`，CPU 额度随申请的 GPU 分配。

```bash
#!/usr/bin/env bash
#SBATCH --account=proj-sai-hpc-software
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=2
#SBATCH --ntasks=4
#SBATCH --ntasks-per-node=2
#SBATCH --gpus-per-node=1
#SBATCH --export=ALL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/my-case

set -eo pipefail
source /etc/profile.d/lmod.sh
module purge
module use /org/org-open-sai/proj-sai-hpc-software/sif/20261024-r4/modulefiles
module load cp2k-sif/master-20261024-gcfe5e5d87aaf

export OMP_NUM_THREADS=1
umask 077
export TMPDIR="$PWD/.mpi-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher
export PRTE_MCA_plm_slurm_args=--external-launcher
mpirun -np 4 --map-by ppr:2:node:PE=1 --bind-to core cp2k.psmp -i input.inp
```

公共安装后，公共 module 已在通常的 module 路径中，不需要上面的项目
`module use`。`echo "$SAI_SIF_CP2K_IMAGE"` 可查看实际选择的 SIF，其他程序使用
对应的 `SAI_SIF_ABACUS_IMAGE`、`SAI_SIF_LAMMPS_IMAGE` 等变量。

ABACUS、CP2K、LAMMPS 由**宿主机** `mpirun` 启动；GPUMD 和 `dp` 直接执行各自
命令。`deepmd-python script.py` 使用该 SIF 内的 Python 包，不改变宿主机默认
`python`。LAMMPS 绑定它原先验收的物理 DeePMD 3.2.0，独立 DeePMD 开发版不参与
LAMMPS 的依赖选择。

工作目录按原绝对路径读写绑定，输入、输出及用户模型应放在工作目录或其
子目录。镜像中的程序、私有库和 CP2K data 保持原安装前缀。`TMPDIR` 必须提前
创建在家目录或项目目录，并在启动宿主 MPI **之前**导出。作业结束后，这个
独立作业临时目录可以清理。宿主 MPI 需要用户环境，因此本示例使用
`--export=ALL`；直接使用 `--export=NIL` 会令站点 MPI 无法取得用户家目录。

## 执行方式与开销

```text
宿主 mpirun / PRRTE
  → 每个 rank 的轻量入口
  → apptainer exec --sharens
  → SIF 中的程序 + 宿主 MPI / UCX / GPU 驱动
```

程序和私有库由只读 SquashFS 按需读取，没有预先完整解包或可写 overlay。
宿主 `/opt/devtools` 提供已匹配的 MPI 等依赖；只绑定明确的外部软件依赖，
不绑定整个 `/opt/apps`。不创建虚拟网络，CPU affinity 和 GPU/MPI 环境会传给程序。
参数按 argv 传递，含逗号的 CUDA/UCX 环境变量通过 `APPTAINERENV_*` 与 `--no-eval`
传递。

Apptainer 1.4.4 的 `--sharens` 让同节点、同 PRRTE 父进程启动的 MPI rank 共享
user namespace，解决 UCX POSIX 共享内存访问另一 rank 的权限问题。入口一路
使用 `exec` 保持父 PID；Apptainer 配置目录位于作业的节点临时目录。
同一 `mpirun` 调用内应使用同一镜像；如果另写每个 rank 的 shell 启动脚本，
最后应使用 `exec cp2k.psmp …` 等形式，保持共同父进程。
Open MPI 的 `shmem_mmap_relocate_backing_file=1` 将通信内存池放到 `/dev/shm`
（RAM），避免在 Lustre 上建立共享内存池。已有用户显式设置会保留。
容器 `/tmp`、`/var/tmp` 的磁盘存储绑定到项目/家目录，不使用物理 `/tmp`。

额外成本包括容器建立、动态加载、压缩块读取，以及首次完整 SIF SHA256 校验。
校验由同一作业、节点上的 rank 通过锁共享；后续调用只核对文件身份、大小和
时间戳。镜像必须只读，变化后会重新校验。不能把总开销描述成只有一次可执行
文件读取，也不能把启动开销算成每个计算步都会重复发生。

此前同产物的 CP2K 2026.2 对照中，启动额外开销中位数约 0.400 s；两节点、八张
V100 的 water216 中位总耗时为 native 223.874 s、SIF 224.633 s（+0.34%）。完整
镜像校验在该计时之外。这是特定工作负载的证据，不是所有程序的性能界限：
[单节点](../../docs/evidence/cp2k-container-overhead-20261020/README.md)、
[两节点](../../docs/evidence/cp2k-container-multinode-20261021/README.md)。

本轮新入口通过 17 个软件/分区数值检查，包含 16V100 两节点四 rank、其他分区
同节点双 rank，以及 LAMMPS GPU、DeePMD PyTorch GPU 和 GPUMD 计算。
作业 `1919703`–`1919706` 均为 `COMPLETED 0:0`。新入口没有重新建立全面性能基线。

## 管理员安装

交付包会将镜像复制到管理员可读的部署目录，避免管理员访问私有构建目录。
`prepare-handoff.py` 要求四个分区的入口检查全部完成、runtime/catalog 哈希一致，
并核对成功的 Slurm accounting；复制后的 SIF 再次逐一校验。

准备好的服务器目录是：

```bash
cd /home/stardust/sai-hpc-software/deployment/sif-development-20261024
bash install.sh --check
bash install.sh --install
```

`--check` 只读核对。`--install` 创建 `/opt/apps/sai-sif/20261024`，并将五个
`*-sif` module 安装到 `/opt/modules/modulefiles/apps`。已有同名目录/版本会阻止
覆盖。`/opt` payload 仅包含 SIF、必要启动入口/环境、身份清单和 module；检查
脚本、模型、日志和编译文件不会复制到 `/opt`。原有物理安装不参与复制。

安装后新 shell 可用 `module --ignore_cache avail cp2k-sif` 绕过旧的 Lmod 缓存。
公共 SIF 仍依赖该集群已安装、已验收的 MPI/CUDA/框架等版本；该部署不是一个
独立于站点的软件环境。
