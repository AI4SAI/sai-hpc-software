# ABACUS 3.11.0-beta12 管理员安装

将已验收的 `v3.11.0-beta12@6b24db05dc175d6e1d7894ac380f48118b43fbaa`
四分区产物安装到物理 `/opt`。无需重新编译。

```bash
cd /home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta12
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

由有目标目录写权限的 `saiuser01` 执行。`--install` 自带完整预检，因此可以省略单独的
`--check`。安装成功输出 `ABACUS_NATIVE_INSTALLED`，文件复验成功输出
`ABACUS_NATIVE_VERIFIED`。已有相同内容可重复安装；不同文件或 module 会阻止覆盖。

安装脚本核对四份 SIF、来源、验收记录、原生导出清单及 SHA256，再通过
`apptainer sif list` 和 `unsquashfs -cat` 提取选中的运行文件，不执行镜像内的代码。
每分区在目标文件系统内暂存并逐文件回读后更名，最后发布 module。
`--verify` 只读取已安装文件和交付清单，无需原 SIF。

## 文件结构

```text
/opt/apps/abacus/abacus-release-v3.11.0-beta12-g6b24db05dc17-r20e07b5cdfb3/
├── 16V100/
├── 4V100/
├── 8V100V0/
└── DSPRHBM/

每个分区：
├── bin/
│   ├── abacus -> abacus_max_gpu      # DSPRHBM 为 abacus_max_para
│   └── abacus_max_gpu               # DSPRHBM 为 abacus_max_para
├── dependencies/
│   ├── libtorch/
│   │   ├── build-hash
│   │   ├── build-version
│   │   └── lib/                    # LibTorch 共享运行库
│   └── nep/lib/libnep.so
└── share/licenses/                 # 随构建交付的依赖许可证

/opt/modules/modulefiles/apps/abacus/3.11.0-beta12-auto
```

每分区 19 个普通文件及 1 个相对链接；四分区合计约 **1.84 GiB**。
详细路径、字节数、权限及 SHA256 固定在 `runtime-plan.json`。

| 分区 | 运行文件大小 |
| --- | ---: |
| 16V100 | 472.29 MiB |
| 4V100 | 472.90 MiB |
| 8V100V0 | 472.18 MiB |
| DSPRHBM | 463.12 MiB |

MPI、ELPA、LibXC、FFTW、SAIBLAS、CUDA、cuSOLVERMp/NCCL 复用已验收的站点依赖。
源码、头文件、静态开发库、自写测试、工作流元数据和 SIF 不进入应用安装目录。
原 beta11 安装及其 module 保留，新版使用独立版本路径。

管理员交付包中的 `images/` 是四个只读压缩 SIF，合计约 **702 MiB**。
家目录不展开原生软件树，也不要求管理员访问受限的 `/org` 路径。
安装后用户直接执行物理 `/opt` 中的程序，运行时不依赖家目录或 Apptainer。

## 用户加载

在对应分区的 Slurm 作业内：

```bash
module load abacus/3.11.0-beta12-auto
mpirun ... abacus
```

module 根据分区选择程序和已验收的依赖，输出版本、源码摘要和安装路径。
登录节点加载时先显示帮助再拒绝加载。可直接运行
`module help abacus/3.11.0-beta12-auto` 查看说明。
若列表未刷新，可用 `module --ignore_cache avail abacus` 查询。

## 安装后运行验收

管理员安装后，`stardust` 执行：

```bash
cd /home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta12
bash submit-native-checks.sh all
```

此脚本使用项目账户 `proj-sai-hpc-software`，所有新结果及 scratch 写入
`/org/org-open-sai/proj-sai-hpc-software`。输入和数值参考取自 beta12 已验收的原始记录。

每分区检查实际公共 module、版本/功能、物理程序、动态库和 MPI 路径，执行单节点与
双节点 PW/HSE/DeePKS；GPU 分区另测 cuSOLVERMp/NCCL。合计 30 项科学计算，能量
容差保持 `1e-5 eV`。验收脚本和原始记录保留在交付/项目目录，均不进入 `/opt`。
脚本会输出作业号、结果目录及完成后的复验命令。

## 已有构建证据

四份 SIF 和原生导出已于 2026-11-03 通过构建与科学验收。
[原始验收记录](../../docs/evidence/prerelease-ecc-recovery-20261103/README.md)
保留全部成功和失败尝试；2026-11-05 重新核对的 19 个功能/跨节点作业均为
`COMPLETED 0:0`。

四份真实 SIF 已在项目目录完成试安装、重复安装和完整文件复验，全部通过；
管理员权限、登录节点 module 帮助和 beta12 原始参考材料也已核查。
见[交付验证记录](../../docs/evidence/abacus-beta12-handoff-20261105/README.md)。

2026-11-05 管理员完成物理 `/opt` 安装后，四分区文件/module 复验及全部 30 项
原生科学计算通过。作业为 4V100 `2217408`、16V100 `2217409`、8V100V0
`2217410`、DSPRHBM `2217411`，均为 `COMPLETED 0:0`。最大参考能量误差
`2.05e-8 eV`，低于 `1e-5 eV` 阈值；MPI、实际 GPU 后端和 module 卸载均通过。
详见[安装后原生验收](../../docs/evidence/abacus-beta12-installed-20261105/README.md)，
其中保留 CPU 分区的 MPI 共享内存警告及较长耗时，不将本轮结果作为性能标定。
