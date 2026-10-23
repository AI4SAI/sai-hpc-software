# ABACUS 3.11.0-beta11 管理员安装

将已验收的 `v3.11.0-beta11` 四分区容器产物安装到物理 `/opt`，不重新编译。
入口沿用 CP2K/LAMMPS：`install.sh --check`、`--install`、`--verify`，以及
安装后的 `submit-native-checks.sh all`。

服务器准备目录：

```text
/home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta11
```

## 管理员执行

以有目标目录写权限的 `saiuser01` 执行：

```bash
cd /home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta11
bash install.sh --install
```

`--install` 自带完整预检，无需先重复运行 `--check`。如果只想检查源文件和安装冲突：

```bash
bash install.sh --check
```

脚本核对四份 SIF、发布记录、原生清单和全部选中的运行文件 SHA256。
在目标文件系统建立隐藏暂存目录，复制后回读文件内容、权限和符号链接，
再更名为最终目录。四分区全部就绪后，原子发布一个公共 module：

```text
/opt/modules/modulefiles/apps/abacus/3.11.0-beta11-auto
```

精确安装前缀为：

```text
/opt/apps/abacus/abacus-release-v3.11.0-beta11-gb0411118d360-r92ecfac1f62a/4V100
/opt/apps/abacus/abacus-release-v3.11.0-beta11-gb0411118d360-r92ecfac1f62a/16V100
/opt/apps/abacus/abacus-release-v3.11.0-beta11-gb0411118d360-r92ecfac1f62a/8V100V0
/opt/apps/abacus/abacus-release-v3.11.0-beta11-gb0411118d360-r92ecfac1f62a/DSPRHBM
```

安装成功输出 `ABACUS_NATIVE_INSTALLED`。已有内容完全相同的安装可以重复执行；
遇到不同内容会停止并保留原状。旧安装、旧 module 和默认版本保持不变，
失败的暂存目录保留供检查。脚本不修改站点 module 缓存配置。

## 安装内容与权限

每分区保留 `bin/abacus` 链接及真实可执行文件、私有 LibTorch/NEP 共享运行库、
标准版本信息和许可证，共 19 个普通文件及 1 个链接。
不复制工作流元数据、自写测试、内嵌 module、私有依赖头文件、静态库和上游测试库。
验收输入、赝势/轨道测试材料、DeePKS 测试模型和结果留在家目录；
用户计算仍使用其自己的输入、赝势、轨道和模型。

| 分区 | 标准安装大小 |
| --- | ---: |
| 4V100 | 471.56 MiB |
| 16V100 | 470.97 MiB |
| 8V100V0 | 470.85 MiB |
| DSPRHBM | 462.16 MiB |

合计约 1.83 GiB。MPI、ELPA、LibXC、FFTW、SAIBLAS、CUDA、cuSOLVERMp/NCCL
复用已验收的站点依赖；公共 module 直接选物理前缀，不依赖家目录或 Apptainer。
8V100V0 使用本次已验收的 AVX2 依赖，ABACUS 自身仍是该分区的 AVX512 构建。

已审计 `saiuser01` 的有效 POSIX 权限，补充阻挡源路径的只读/穿越 ACL。
该账号可读取安装与验收材料，并可写入 `/opt/apps/abacus` 和
`/opt/modules/modulefiles/apps/abacus`；没有授予源文件写权限。
换账号时，源目录所有者可使用 `source-access.py --account 账号 --report 家目录新文件.json`
做只读审计；显式 `--apply` 仅补充本发布所需的源读取/穿越权限，不修改 `/opt` 权限。

## 安装后验证

先校验物理安装文件，不需要原 SIF 或 `.native`：

```bash
bash install.sh --verify
```

再以普通集群账号提交四分区原生运行检查：

```bash
bash submit-native-checks.sh all
```

可将 `all` 换为 `4V100`、`16V100`、`8V100V0` 或 `DSPRHBM`。
脚本先校验已安装文件及固定的参考材料，再提交每分区一个双节点 Slurm 作业。
GPU 分区每节点 1 个 rank/1 张 GPU，CPU 数由站点分配，线程数为 1；
DSPRHBM 每节点 4 个 rank、每 rank 2 线程，共 16 CPU。时限均为 40 分钟。

每个分区检查公共 module、版本/功能、两节点实际二进制和 MPI/ELPA/LibTorch/NEP
加载路径，并执行 PW、HSE、DeePKS 的单节点和双节点计算。
GPU 分区另跑实际 cuSOLVERMp 分布式对角化和 NCCL 集合通信，共计 30 项科学计算。
HSE/DeePKS 沿用已验收输入的 CPU 算法，GPU 始终可见；PW 在 GPU 分区使用 GPU。
单 GPU rank 的 PW 输入只将 `kpar` 从 2 改为 1。
能量与该分区已验收 SIF 结果及可用的上游参考比较，容差仍为 `1e-5 eV`。
这一步是安装正确性验证，不是大型生产体系性能测试。

所有运行直接执行物理 `/opt` 的 ABACUS，不启动容器。
提交时将控制脚本和输入固定到新结果目录，避免排队期间修改影响作业。
输出及 MPI/CUDA 临时文件均在执行账号自己的家目录，不使用节点 `/tmp`；
可用 `SAI_ABACUS_NATIVE_WORK_ROOT` 指定该账号家目录内的工作根。

提交脚本会打印结果目录和完成后的复验命令：

```bash
python3 native_checks.py verify /home/你的账号/sai-hpc-software/runtime-tests/abacus-beta11-physical-时间戳-进程号
```

复验器重新解析原始结果、核对文件校验和、module 卸载和 Slurm 的 `COMPLETED / 0:0`。
全部通过时输出 `"passed": true` 并返回 0。排队、运行中、失败或缺少证据均返回非零。

## 用户加载

获得对应分区的 Slurm allocation 后：

```bash
module use /opt/modules/modulefiles/apps
module load abacus/3.11.0-beta11-auto
mpirun ... abacus
```

module 只负责分区和依赖选择，计算所需 rank、线程及 GPU 设置由作业指定。
如果 `module avail` 暂未显示新条目，可用
`module --ignore_cache avail abacus` 刷新本次查询，再按完整名称加载；无需替换旧默认版本。

## 当前验证状态

2026-10-23 管理员已完成四分区物理 `/opt` 安装。普通 `stardust` 账号执行
`install.sh --verify` 通过，独立回读确认每分区 19 个普通文件的 SHA256、
1 个符号链接及可执行权限均符合安装清单。公共 module 内容与发布包一致，
普通查询及 `--ignore_cache` 查询均可见。

原生运行验收已提交：4V100 `1903636`、16V100 `1903637`、
8V100V0 `1903638`、DSPRHBM `1903639`。截至记录时四项均为 `PENDING`，
调度器显示 `AssocGrpJobsLimit`；用户另反馈当前账号额度已用完。
任务及冻结输入保留，30 项原生科学计算尚未执行，不能据此宣称原生运行验收通过。
后续有额度且可读参考材料的普通账号可直接使用上述提交命令；
同一账号恢复后先检查已有任务，避免重复提交。

已提交任务的结果目录：

```text
/home/stardust/sai-hpc-software/runtime-tests/abacus-beta11-physical-20261023-074725-2961798
```

[物理安装与待运行验收记录](../../docs/evidence/abacus-beta11-20261022/physical-installation.json)
包含实际文件、公共 module、冻结输入校验和及提交时的 Slurm 状态。

原构建和数值验收见 [release 交付记录](../../docs/evidence/abacus-beta11-20261022/README.md)。
