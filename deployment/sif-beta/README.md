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
  master-beta-later -> master-beta-20261024-cfe5e5d87aaf
  master-beta-20261024-cfe5e5d87aaf
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

输入、模型和输出保留在 `/org` 下自己的算例目录。当前冻结 runtime 仍要求在启动
宿主 MPI 或程序前设置已有的项目 `TMPDIR`；上述命令不代表已实现自动内存临时区。
将 MPI socket、容器配置和少量缓存自动放入节点内存的改造尚未验收。计算节点
`/dev/shm` 为 tmpfs，但现有 epilog 按用户清理；需要先解决并发作业隔离，且启用
swap 的节点不能保证 tmpfs 绝不写入物理盘。移除独立模板不会改变已验收 runtime。

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

实际双卡检查使用 SIF 内的 `deepmd-python -m torch.distributed.run`，让 launcher
和 worker 使用同一份 Python。两张 V100 的 NCCL all-reduce 通过，但不能据此
判定训练通过：当前提交的 `se_e2_a` 在第二步触发 DDP 未使用参数错误，参数
8–11 是仍标记可训练的空 `compress_info`/`compress_data`。DPA2 训练则在 Triton
查找 CUDA 库时遇到 SIF 缺少 `/sbin/ldconfig`。这些失败及后续修复要求记录在
[实际检查记录](../../docs/evidence/sif-beta-site-20261024/README.md)。
双卡训练没有通过验收；帮助中只推荐已验收的 `dp --pt train input.json` 单卡模式，
旧的 GPU 推理数值验收保持其原有范围。

当前 GPUMD
[main](https://github.com/brucefan1983/GPUMD/blob/2c93346fce4461de1c42c77e7fe74cdb6b0e3df6/src/main_gpumd/main.cu)
没有 MPI 初始化；构建使用 nvcc，实际二进制没有 MPI/PMPI 导入。
其 PLUMED 依赖可以间接链接 MPI，但 GPUMD 的计算并不通过 MPI rank 分解。
`mpirun -np N gpumd` 会启动多份独立计算。应在算例目录直接运行 `gpumd`。
NEP 计算可以在单进程内自动使用多张可见 GPU；这仍然直接运行 `gpumd`，并且
模拟盒必须足够大。本次已验收的是单卡；双卡小盒检查触发了 GPUMD 自身的
尺寸检查，不能把申请更多 GPU 当成对所有算例都适用的加速方式。

## 轮换与复现

固定文件命名为 `<branch>-beta-<YYYYMMDD>-<12位SHA>`。每个文件绑定不可变
快照配置和启动器；加载时保存 generation，软链接轮换后，旧环境的执行与卸载
继续使用旧配置。保留旧模块、SIF 和依赖，不自动清理历史产物。
历史 generation 和验收记录中的模板保持原样；后续 generation 只发布模块与清单。
需要复现时直接加载固定名称，例如 `cp2k/master-beta-20261024-cfe5e5d87aaf`；
可先 `module use /home/stardust/sai-hpc-software/modulefiles/apps` 查看固定版本。

stardust 在验收后发布（同名固定版本不覆盖）：

```bash
python3 -B controller/sif_beta.py promote \
  --root /home/stardust/sai-hpc-software/sif-beta \
  --module-root /home/stardust/sai-hpc-software/modulefiles/apps \
  --generation 20261024-r2 \
  --bundle /home/stardust/sai-hpc-software/deployment/sif-development-20261024/payload \
  --validated-catalog /org/org-open-sai/proj-sai-hpc-software/sif/20261024-r4/catalog.json \
  --checks /home/stardust/sai-hpc-software/deployment/sif-development-20261024/validation.json
```

它核对完整分区验收、catalog/runtime 绑定、镜像 SHA256 和启动器，准备固定文件后
原子替换软链接。`--software cp2k` 可只更新一个程序。旧的 `master-beta`/
`develop-beta` 入口与历史记录保留，新版维护工具只更新 `*-beta-later` 链接。
回滚到采用新布局的已保留 generation：

```bash
python3 -B controller/sif_beta.py select \
  --root /home/stardust/sai-hpc-software/sif-beta \
  --generation 20261024-r2 --software cp2k
```

## 管理员安装

```bash
cd /home/stardust/sai-hpc-software/deployment/sif-beta
bash install.sh --check
bash install.sh --install
```

只安装五个固定转发模块。首次可用 `module --ignore_cache avail beta-later` 发现
新名称；`module help/load` 直接读取家目录当前选择，日常轮换不用刷新静态入口。
