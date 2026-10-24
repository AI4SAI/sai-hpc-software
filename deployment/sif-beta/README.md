# 开发分支 beta 模块

| 程序 | 固定入口 | 支持分区 |
| --- | --- | --- |
| ABACUS | `abacus/develop-beta` | 4V100、16V100、8V100V0、DSPRHBM |
| CP2K | `cp2k/master-beta` | 同上 |
| LAMMPS | `lammps/develop-beta` | 三个 V100 分区 |
| DeePMD-kit | `deepmd-kit/master-beta` | 三个 V100 分区 |
| GPUMD | `gpumd/master-beta` | 三个 V100 分区 |

beta 指开发分支中最近通过验收的快照，未必等于此刻上游 HEAD。
`/opt/modules/modulefiles/apps` 中的五个文件仅转读
`/home/stardust/sai-hpc-software/modulefiles/apps` 下的对应模块。
版本、帮助、模板以及轮换指针由 stardust 在家目录维护；管理员安装一次即可。
程序仍通过已验收的固定 SIF/启动器运行，不需要每日复制到 `/opt`。

## 查看版本和提交作业

管理员安装入口前，先执行：

```bash
module use /home/stardust/sai-hpc-software/modulefiles/apps
```

登录节点可运行：

```bash
module help cp2k/master-beta
module load cp2k/master-beta
```

第二条命令会输出帮助，并以非零状态返回；不会成功加载模块或其依赖。
帮助包含完整源码提交、源码版本、快照日期、可用分区、固定版本入口和模板目录。
登录节点上的 `salloc` 仍不加载；应在 `sbatch` 脚本或 `srun` 启动的计算节点 shell
内加载。未知分区也会输出帮助并拒绝加载。

从帮助列出的模板目录复制 `gpu.sbatch` 到自己的算例目录，修改 `YOUR_ACCOUNT`、
分区、QoS、资源与输入文件名后执行 `sbatch`。CP2K/ABACUS 另有 `cpu.sbatch`。
模板是可编辑的提交示例，输入文件、模型、赝势等由用户准备；不提交共享模板本身。
GPU 模板不设置 `--cpus-per-task`，CPU 配额随 GPU 分配；示例 rank/OpenMP 数量
可按体系规模调整。若调整 MPI rank 数量，也要同步修改 `--map-by`。

SIF 模板与物理安装版本的区别：

- 先将 `TMPDIR` 建在自己的 `/home` 或 `/org` 算例目录，再启动宿主 MPI。
- 输入、模型、输出放在算例目录或其子目录，容器按原绝对路径访问。
- CP2K、ABACUS、LAMMPS 使用宿主 `mpirun` 启动模块提供的命令；不嵌套 MPI。
- GPUMD、`dp` 直接执行；`deepmd-python` 选择 SIF 内的 Python 环境。
- 命令本身负责 SIF 挂载与运行，不要替换成猜测的物理 `/opt` 可执行文件路径。

成功加载后会输出短版本提示和模板目录，每次加载一次，不按 MPI rank 重复。
例如 CP2K 的当前选择可以查看：

```bash
echo "$SAI_SIF_CP2K_SOURCE"
echo "$SAI_SIF_CP2K_IMAGE"
echo "$SAI_SIF_CP2K_TEMPLATES"
```

## 固定版本与轮换

加载 beta 时保存不可变 generation，PATH 直接指向该快照的固定启动器。
轮换不会改变已加载的镜像、PATH 或模板；退出作业、指针变化后也能正确卸载。
保留旧 generation、固定模块、SIF、启动器及所需依赖，不自动删除旧产物。

需要精确复现时，使用帮助中的 `*-sif/<固定版本>` 名称，配合家目录 `module use`。
beta 若在作业开始时加载，会选择那一刻的最近验收快照；排队时不会提前固定。
已加载 beta 后查看帮助，会显示当前环境固定的版本。

轮换按程序独立进行，要求该程序全部目标分区验收通过。LAMMPS 保持其原先验收的
物理 DeePMD 依赖；独立 DeePMD beta 的成功或失败不会改变这个依赖。

stardust 在仓库中执行以下维护命令（路径均指已有的不可变快照）：

```bash
python3 -B controller/sif_beta.py promote \
  --root /home/stardust/sai-hpc-software/sif-beta \
  --module-root /home/stardust/sai-hpc-software/modulefiles/apps \
  --generation 20261024-r1 \
  --bundle /home/stardust/sai-hpc-software/deployment/sif-development-20261024/payload \
  --validated-catalog /org/org-open-sai/proj-sai-hpc-software/sif/20261024-r4/catalog.json \
  --checks /home/stardust/sai-hpc-software/deployment/sif-development-20261024/validation.json
```

工具核对科学验收状态、完整分区、实际 SIF/MPI 检查所绑定的 catalog/runtime、
移动后镜像的来源映射、完整镜像 SHA256 和固定启动器，然后准备版本/模板，
最后原子替换各程序的小指针文件。`--software cp2k` 可只更新一个程序。
镜像及启动器必须对用户可读/可执行；公共入口不可依赖仅项目成员可访问的构建树。
这一步由维护者在验收后执行；构建成功本身不会自动轮换入口。

回滚到已保留的 generation，不修改 `/opt`：

```bash
python3 -B controller/sif_beta.py select \
  --root /home/stardust/sai-hpc-software/sif-beta \
  --generation 20261024-r1 --software cp2k
```

## 管理员一次性安装

```bash
cd /home/stardust/sai-hpc-software/deployment/sif-beta
bash install.sh --check
bash install.sh --install
```

只安装五个固定转发模块，不复制 SIF、测试或构建目录。
相同入口可重复安装，不覆盖已有不同内容的模块。
首次发现新名称可用 `module --ignore_cache avail beta`。
之后名称不变，`module load/help` 每次读取家目录内当前选择，轮换不依赖刷新
`module avail/spider` 的缓存；缓存中的描述不是版本依据。
