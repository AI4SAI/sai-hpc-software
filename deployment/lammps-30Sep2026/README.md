# LAMMPS 30 Sep 2026 / DeePMD 3.2.0

本目录交付 4V100、16V100、8V100V0 三个 GPU 分区的已验收产物。
每份均包含 LAMMPS 和配套 DeePMD，保留 GPU package、Kokkos CUDA/SM70、
MPI/OpenMP、PLUMED 2.10.1，以及 TF/PT/JAX 后端。
站点 PyTorch 后端使用 CUDA；TF/JAX 继续保留其数值正确性验收。
性能验收范围是 PyTorch GPU，不包含纯 CPU 性能比较。

2026-10-21：`saiuser01` 已完成六个标准运行目录及公用 module 的物理安装。
`stardust` 独立复核全部安装文件，并完成三分区原生 GPU 验收：
4V100 `1885341`、16V100 `1885342`、8V100V0 `1885343` 均为 `COMPLETED / 0:0`。
每分区 17 项科学检查、完整功能清单、动态库/MPI 路径及 module 卸载均通过，
原始结果复验和运行后安装文件校验也通过。
详见 [物理安装验收记录](../../docs/evidence/lammps-release-20261020/physical-native-acceptance.json)。

服务器准备目录：

```text
/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026
```

## 直接使用已验收的容器

在这三个分区之一的 Slurm allocation 内加载：

```bash
module use /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/modulefiles
module load lammps/30Sep2026-deepmd320-gpu
lmp -h
```

这个 module 根据 `SLURM_JOB_PARTITION` 选择该分区的已发布镜像和启动器。
运行 `lmp` 即调用容器中的程序，外部 MPI/CUDA 继续使用站点 module。
GPU package 可用 `-sf gpu -pk gpu 1`；Kokkos 可用
`-k on g 1 t 1 -sf kk -pk kokkos gpu/aware off`。
具体模型需有对应加速 style；DeePMD 的 PyTorch CUDA 路径不需要这两个 suffix。

## 安装到物理 /opt

`release.json` 固定三个已发布镜像、发布记录、六个配套安装目录，以及全部清单的 SHA256。
`runtime-plan.json` 从原始清单选择标准运行文件，保留精确编译前缀。
DeePMD 和 LAMMPS 必须一起安装，
不能只复制 `lmp`。GCC、MPI、CUDA、PLUMED 和后端环境继续引用已有站点依赖。

`/opt` 中保留可执行文件、共享库、DeePMD/DPA Python 包及包版本信息、
`pyvenv.cfg`、全部 287 个 LAMMPS 势函数文件，以及标准头文件、CMake/pkg-config
接口和 man 手册。头文件及开发接口很小，保留它们支持用户调用程序库和编译插件。
不复制 `share/sai` 工作流元数据/自写测试、内嵌 module、上游 `bench` 输入、
pip/环境激活脚本或 Python 字节码缓存。原始导出保持完整；验收脚本、参考材料、
SHA256 清单都留在本家目录发布包及原始工作目录中。
每分区的标准安装约 1.09 GiB，比完整导出少约 59.6 MiB 和 1,940 个文件/链接。
公用 module 独立选择配套前缀和 MPI ISA，不再从软件目录或家目录加载 module 片段；
它也禁止 Python 向公用安装目录生成字节码缓存。

默认只做只读预检，可由能读取源文件的账号执行：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/install.sh --check
```

由能读取源文件、并能写入 `/opt/apps/lammps`、`/opt/apps/deepmd-kit`
和 `/opt/modules/modulefiles/apps/lammps` 的账号执行安装。
安装已包含完整预检，无需先重复运行 `--check`：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/install.sh --install
```

脚本校验源 SIF、发布记录、原始清单和每个选中的运行文件；先复制到目标文件系统的隐藏暂存目录，
回读校验全部文件、权限和符号链接，再更名为最终目录。已有相同安装可重入，
不同内容会报错，失败的暂存目录保留。六个目录全部就绪后才发布：

```text
/opt/modules/modulefiles/apps/lammps/30Sep2026-deepmd320-auto
```

管理员已完成本次物理安装。需要复核安装文件时执行：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/install.sh --verify
```

安装不会更改旧版 module 或默认版本。

## 安装后的原生运行验收

完成安装后，用普通集群账号在登录节点提交：

```bash
cd /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026
bash submit-native-checks.sh all
```

可把 `all` 换成 `4V100`、`16V100` 或 `8V100V0` 单独验证。提交前检查选中分区
的物理安装文件和 module，并校验 `native-reference.json` 固定的已验收输入、模型、
原始输出及解析器。全部通过后，才向各分区提交一个双节点作业：每节点 1 个 MPI rank、
1 张 GPU，CPU 数由站点按 GPU 数分配；每 rank 使用 1 个计算线程，时限 40 分钟。
16V100 使用 `flood-1o2gpu`，其余使用 `rush-1o2gpu`。
脚本、模型和输入复制到本次结果目录并固定 SHA256，排队期间仓库更新不影响已提交作业。

每个分区验证版本、71 个 package / 2,473 个 style、动态库解析、MPI ISA、每个 rank
实际执行的二进制和节点，以及 17 项科学检查：

- TF/PT/JAX 各跑单节点和双节点 LAMMPS，比较能量、全部力和 virial，并检查 PLUMED。
- GPU package 和 Kokkos 各跑单节点、双节点的 LJ 与 Coulomb/PPPM，比较能量、压力和全部力。
- 配套 DeePMD Python 的 TF/PT/JAX 推理，核对实际导入路径，防止误用旧安装。

这些作业加载 `/opt` 公用 module，直接执行精确安装前缀下的 `bin/lmp` 和配套
`bin/python`，不调用 Apptainer。GPU 保持可见；TF/JAX 继续使用站点提供的后端，
不新增纯 CPU 性能比较。数值容差沿用已验收容器的标准，并比较单、双节点结果。

脚本输出本次结果目录和 `jobs.tsv`。作业完成后，把下面路径替换成输出的目录：

```bash
python3 native_checks.py verify /home/你的账号/sai-hpc-software/runtime-tests/lammps-30Sep2026-physical-时间戳-进程号
```

验证器重新读取原始日志、dump、COLVAR 和 Python 观测值，核对文件 SHA256、完整算例矩阵、
rank 节点和 module 卸载，并查询 Slurm，要求对应分区 `COMPLETED / 0:0`。
全部通过时输出顶层 `"passed": true` 并返回 0；排队、运行中、失败或缺少证据均返回非零，
逐分区显示原因。单独存在 `native-acceptance.json` 不代表作业最终成功。

结果默认写到执行账号的 `$HOME/sai-hpc-software/runtime-tests/`，MPI scratch 写到
同一工作根的 `runtime/ln-作业号`，CUDA/XDG cache 放在本次结果目录；不使用主机 `/tmp`。
可用 `SAI_LAMMPS_NATIVE_WORK_ROOT` 指定该账号家目录内的另一个工作根。
参考材料默认从 `/home/stardust/sai-hpc-software` 读取，可用 `SAI_SOFTWARE_ROOT` 指定。
提交账号须有这些原始参考文件的读取权限；跨账号使用前可先执行以下只读检查：

```bash
python3 native_checks.py preflight all
```

`source-access.py` 供源目录所有者核查跨账号权限，覆盖发布记录、镜像、运行文件、
验收输入和原始参考输出，以及三个安装目标父目录。默认只审计，显式 `--apply`
才为指定账号补充被阻挡源路径的只读/目录穿越 ACL；不递归开放其他工作目录，
不授予源写权限，不更改 `/opt` 权限。报告路径须尚不存在，例如：

```bash
python3 source-access.py --account saiuser01 --report "$HOME/lammps-source-access.json"
```

失败日志、暂存目录和原始产物全部保留。此交付已通过物理安装后的科学验收，
上述命令可用于后续复核。验收用时反映小型正确性检查，不代表生产体系性能。

验收通过后，在对应分区的 Slurm allocation 内使用：

```bash
module use /opt/modules/modulefiles/apps
module load lammps/30Sep2026-deepmd320-auto
lmp -h
```

完整验收来源见 [LAMMPS-RELEASE.md](../../docs/LAMMPS-RELEASE.md)。
