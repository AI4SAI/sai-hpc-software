# 按系统模板使用开发分支 SIF

| 程序 | 滚动入口 |
| --- | --- |
| ABACUS | `abacus/develop-beta-later` |
| CP2K | `cp2k/master-beta-later` |
| LAMMPS | `lammps/develop-beta-later` |
| DeePMD-kit | `deepmd-kit/master-beta-later` |
| GPUMD | `gpumd/master-beta-later` |

`later` 按用户指定的拼写命名，表示最近通过验收的分支快照。家目录里是真实的
相对软链接，例如：

```text
/home/stardust/sai-hpc-software/modulefiles/apps/cp2k/
  master-beta-later -> master-beta-20261024-cfe5e5d87aaf-ra4c6a24c229f
  master-beta-20261024-cfe5e5d87aaf-ra4c6a24c229f
```

日期是交付快照日期，SHA 是源码提交；不承诺等于此刻上游 HEAD。
`/opt/modules/modulefiles/apps/cp2k/master-beta-later` 是固定 Tcl 转发文件，
仅 `source` 上面的家目录软链接。管理员安装一次后，stardust 只在家目录轮换。
新入口保留完整源提交、分区、固定版本、系统模板路径和启动命令的帮助；登录节点 `module load`
会先输出帮助，再返回未加载状态。计算节点实际加载时打印版本。

## 作业启动

系统模板 `/opt/sbatch_examples` 实际指向 `/opt/sai/examples`。
本仓库不再生成或安装单独的 SIF sbatch 模板，也不再导出 `SAI_SIF_*_TEMPLATES`。
沿用系统已有模板的资源申请、绑核和启动方式，把软件的 `module load` 改为上表入口，
通过命令名调用程序。模块配套入口自动选择分区对应的 SIF 并进入容器；可执行文件、
私有库和数据从只读 SIF 按需读取，不解包软件树。

先查看版本、系统模板位置和该软件的启动命令：

```bash
module help cp2k/master-beta-later
```

管理员安装前，可在登录节点先执行 `module use
/home/stardust/sai-hpc-software/modulefiles/apps`，然后查看帮助或提交作业。
按计费需要使用 `sbatch --account=YOUR_ACCOUNT run.sbatch`。

CP2K/ABACUS/LAMMPS 使用宿主 `mpirun` 启动各 rank。GPU 作业沿用系统
`/opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash` 提供的 `$MAP_OPT`。
例如，在系统模板已经设置资源和绑核的地方使用：

```bash
module load cp2k/master-beta-later
mpirun -np $SLURM_NTASKS --map-by $MAP_OPT cp2k.psmp -i input.inp -o output.out
```

输入、模型和输出保留在 `/org` 下自己的算例目录。启动器自动为宿主 MPI 和
容器创建私有的内存临时区，不要求用户设置 `TMPDIR`，也不在家目录或项目目录
创建运行时缓存。作业自己的 `/dev/shm`、`/tmp` 和 `/var/tmp` 属于独立挂载命名空间，
同一用户的另一个作业结束时，系统 epilog 看不到这些文件。进程退出后由内核回收。
模块加载和卸载不改变调用者的 `TMPDIR`。

`mpirun`/`mpiexec` 的薄入口准备内存环境后执行已验收的**物理机 MPI**；远端 `prted`
同样自动准备。受 AppArmor 限制的节点通过系统 Apptainer 的受支持配置启动，一个
36 KiB、只含挂载目录的独立 SIF 提供兼容入口。它不依赖任何应用程序的 SIF。
少量启动配置和有界校验索引放在节点 `/run/lock` 的 tmpfs；程序数据不放在那里。
整个公共 runtime 约 110 KiB，原有程序 SIF 直接复用，不另复制到家目录或内存。

每作业、每节点、每镜像进行一次完整 SHA256 校验，其余 rank 复用内存索引。
启动仍有容器挂载和校验成本；这里没有宣称容器启动零开销或新的性能加速。
节点的 swap 策略保持原样，tmpfs 不能等同于绝不发生交换 I/O。
四分区共 17 个组合、双节点宿主 MPI、实际同用户并发 epilog 和退出回收均有
[本轮验收记录](../../docs/evidence/sif-memory-runtime-20261025/README.md)。

GPU MPI 路径按每张 GPU 一个 rank 验收。若改成每张 GPU 多个 rank，系统脚本
会启动 MPS，现有
SIF 的隔离 `/tmp` 不暴露宿主 MPS pipe，该路径尚未验收，不能直接套用。
LAMMPS 输入需要选择 GPU 样式（如 `lj/cut/gpu`）；`-pk gpu` 本身不自动转换所有样式。

## DeePMD / GPUMD 的启动方式

DeePMD 并非一律不需要 MPI。上游同一源码提交的
[并行训练说明](https://github.com/deepmodeling/deepmd-kit/blob/558f56962775098b8b554e341c3b37d2f86e6aeb/doc/train/parallel-training.md)
与系统模板一致：单卡直接运行；PyTorch 多卡用 torchrun/DDP/NCCL；TensorFlow
多卡用 Horovod，能由宿主 mpirun 启动。当前 SIF 的环境检查确认没有 Horovod，
因此不把 TensorFlow MPI 训练记作已验收能力。
`mpirun dp --pt train` 不会自动把 PyTorch 训练变成 DDP。

当前 beta SIF 已通过三个 V100 分区的 `se_e2_a`、DPA2 双卡训练和断点续训。
[DDP 修复](../../docs/evidence/deepmd-ddp-20261025/README.md)将仅供推理使用的空压缩参数冻结，
保留模型检查点格式；两个 rank 均执行 CUDA 反向传播，梯度有限且最终参数一致。
`/sbin/ldconfig` 和动态库缓存通过只读挂载提供，Triton 缓存自动放在私有内存临时区。
在已经申请两张 GPU 的作业内运行：

```bash
deepmd-python -m torch.distributed.run --standalone --nproc_per_node=2 -m deepmd --pt train input.json
```

当前验收范围是单节点两张 GPU；更多 GPU 和多节点训练尚未验收。

2026-10-25 的[三分区双卡实测](../../docs/evidence/deepmd-multigpu-20261025/README.md)
记录了修复前的 SIF 失败。我们已经安装到 `/opt` 的 3.2.0 release，以及
`deepmd-kit/3.2.0` 指向的原有 Conda 环境，都通过了三个 V100 分区的
`se_e2_a`、DPA2 双卡训练和断点续训，可用于已验证的 PyTorch DDP 路径。
这两份安装与当前 beta SIF 是不同的交付，具体路径、模块选择和命令见实测记录。
当前 TensorFlow 实际是 CPU 构建，JAX 也只识别 CPU；TensorFlow 多卡还需要
提供 CUDA 后端和 Horovod，不能仅根据模块描述判断支持情况。

当前 GPUMD
[main](https://github.com/brucefan1983/GPUMD/blob/2c93346fce4461de1c42c77e7fe74cdb6b0e3df6/src/main_gpumd/main.cu)
没有 MPI 初始化；构建使用 nvcc，实际二进制没有 MPI/PMPI 导入。
其 PLUMED 依赖可以间接链接 MPI，但 GPUMD 的计算并不通过 MPI rank 分解。
`mpirun -np N gpumd` 会启动多份独立计算。应在算例目录直接运行 `gpumd`。
NEP 计算可以在单进程内自动使用多张可见 GPU；这仍然直接运行 `gpumd`，并且
模拟盒必须足够大。本次已验收的是单卡；双卡小盒检查触发了 GPUMD 自身的
尺寸检查，不能把申请更多 GPU 当成对所有算例都适用的加速方式。

## 轮换与复现

每日构建的固定文件命名为 `<branch>-beta-<YYYYMMDD>-<12位源码SHA>-r<12位runtime SHA>-b<12位构建配方摘要>`。
日期表示验收快照日期；源码版本日期可能更早，这不代表当日没有构建。历史固定名称保持原样。
运行时修复也得到新的固定名称，旧版本不会被覆盖。每个文件绑定不可变
快照配置和启动器；加载时保存 generation，软链接轮换后，旧环境的执行与卸载
继续使用旧配置。保留旧模块、SIF 和依赖，不自动清理历史产物。
历史 generation 和验收记录中的模板保持原样；后续 generation 只发布模块与清单。
需要复现时直接加载固定名称，例如 `cp2k/master-beta-20261024-cfe5e5d87aaf-ra4c6a24c229f`；
可先 `module use /home/stardust/sai-hpc-software/modulefiles/apps` 查看固定版本。

每日工作流在每个软件的构建和科学验收之后调用 `beta.yml`。它要求同一开发分支
SHA 的完整分区矩阵，重新检查原始证据和 SIF 校验和，并通过真实 Lmod/SIF 命令
验证数值结果、宿主 MPI、私有内存临时区以及 DeePMD 双卡训练。全部通过后，
只轮换该软件的家目录链接。release-only 或只选部分分区的请求不轮换 beta；
其他 release track 失败不会阻止已经完整通过的 development 发布。

失败保留当前公开版本，并在 Actions 的 beta artifact 和
`/org/org-open-sai/proj-sai-hpc-software/operations/daily-beta/` 保存状态和原始证据。
GPU ECC 故障只允许一次排除坏节点的补验收；数值错误不自动放行。
断线重连会重新附着已有作业，旧工作流不能覆盖更新的选择。
`/opt` 入口无需每日改动；计算和验收文件全部留在项目目录，不放进公共软件目录。

需要手动恢复已存在的完整每日产物时，可使用 `Validate and rotate development beta`
的 workflow dispatch，提供原构建 run ID、attempt 和原始 resolved development matrix JSON。
这条入口使用相同验收门槛，不重新解析移动中的上游分支。

独立维护场景仍可由 stardust 在验收后发布（同名固定版本不覆盖）：

```bash
python3 -B controller/sif_beta.py promote \
  --root /home/stardust/sai-hpc-software/sif-beta \
  --module-root /home/stardust/sai-hpc-software/modulefiles/apps \
  --generation "$GENERATION" \
  --bundle "$BUNDLE" \
  --validated-catalog "$CATALOG" \
  --checks "$CHECKS"
```

变量分别指向新的 generation、公共 runtime、实际验收的 catalog 和完整验收报告。
它核对完整分区验收、自动内存临时区、catalog/runtime 绑定、镜像 SHA256 和启动器，准备固定文件后
原子替换软链接。`--software cp2k` 可只更新一个程序。旧的 `master-beta`/
`develop-beta` 入口与历史记录保留，新版维护工具只更新 `*-beta-later` 链接。
回滚到采用新布局的已保留 generation：

```bash
python3 -B controller/sif_beta.py select \
  --root /home/stardust/sai-hpc-software/sif-beta \
  --generation 20261025-memory-r1 --software cp2k
```

## 管理员安装

```bash
cd /home/stardust/sai-hpc-software/deployment/sif-beta
bash install.sh --check
bash install.sh --install
```

`--check` 复核家目录当前 generation 的验收报告、启动器、runtime、bootstrap 和
17 份 SIF 的完整校验和，也能发现管理员账号的读取权限问题。`--install` 先做相同检查，
只安装五个固定转发模块，不向 `/opt` 放测试、模板或软件副本。首次可用 `module --ignore_cache avail beta-later` 发现
新名称；`module help/load` 直接读取家目录当前选择，日常轮换不用刷新静态入口。
