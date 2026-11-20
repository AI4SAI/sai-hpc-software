# ABACUS LTS 2026-11-20 管理员安装

安装已构建并完成基础科学验收的四分区 LTS 快照。源码为 `deepmodeling/abacus-develop` 的 `LTS@0d967f1f1bc39c16e3bf435e5f02281c72daaf05`，提交日期 2026-11-16；程序自报 `v3.10.1`。module 名中的 20261120 是四分区完成后的快照日期。

由有目标目录写权限的 `saiuser01` 执行：

```bash
cd /home/stardust/sai-hpc-software/deployment/abacus-lts-20261120
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

`--check` 为只读预检；`--install` 包含预检、同文件系统暂存、逐文件验证、目录更名和最后发布 module；`--verify` 核对已经安装的文件。相同内容可重复安装；已有不同内容会停止，不覆盖。成功标记分别是 `ABACUS_LTS_NATIVE_PREFLIGHT_PASSED`、`ABACUS_LTS_NATIVE_INSTALLED`、`ABACUS_LTS_NATIVE_VERIFIED`。

管理员只需读取此家目录交付包，不需要项目目录权限。包中包含约 689 MiB 的压缩 SIF、源码身份、原始科学计算日志、Slurm 成功记录和逐文件校验清单。脚本只从 SIF 读取文件内容，不执行镜像内的安装脚本。编译、计算与暂存试装均位于 `/org/org-open-sai/proj-sai-hpc-software`。

## 安装内容

保持编译时的原始绝对前缀，以保留运行库查找行为：

```text
/opt/apps/abacus/
├── abacus-development-LTS-2026-11-16-g0d967f1f1bc3-rf650f4584906/
│   ├── 4V100/
│   ├── 16V100/
│   └── 8V100V0/
└── abacus-development-LTS-2026-11-16-g0d967f1f1bc3-r95113744402d/
    └── DSPRHBM/

每个分区：
├── bin/abacus
├── dependencies/libtorch/
│   ├── build-hash
│   ├── build-version
│   └── lib/*.so*
└── share/licenses/

/opt/modules/modulefiles/apps/abacus/LTS-20261120-auto
```

每分区 16 个普通文件，四分区合计约 1.81 GiB。源码、头文件、静态开发库、测试和工作流元数据不进入公开应用目录。完整清单见 `runtime-plan.json`。旧版 LTS 和其他版本的独立目录及 module 保留。

| 分区 | 安装大小 | 基础科学验收 |
| --- | ---: | --- |
| DSPRHBM | 454.99 MiB | PW、HSE、DeePKS |
| 16V100 | 465.85 MiB | GPU PW、HSE、DeePKS、GPU cuSOLVERMp |
| 8V100V0 | 465.79 MiB | GPU PW、HSE、DeePKS、GPU cuSOLVERMp |
| 4V100 | 466.27 MiB | GPU PW、HSE、DeePKS、GPU cuSOLVERMp |

MPI、ELPA、LibXC、FFTW、SAIBLAS 和 CUDA 使用站点预装依赖。LTS 的 cuSOLVERMp 使用 CAL 接口，module 将 NVHPC 25.7 的 CUDA 12.9 数学库放在较新数学库之前，与验收 SIF 一致；不会安装或覆盖公共基础库。

## 用户加载

在支持分区的 Slurm 作业中：

```bash
module load abacus/LTS-20261120-auto
mpirun ... abacus
```

根据 `SLURM_JOB_PARTITION` 选择程序及依赖。运行时使用物理 `/opt` 程序和宿主 MPI，不依赖家目录或 Apptainer。加载会显示快照日期、提交摘要、分区和实际路径。

登录节点可执行 `module whatis abacus/LTS-20261120-auto` 和 `module help abacus/LTS-20261120-auto`。`whatis` 内容为：

```text
ABACUS LTS v3.10.1; snapshot 2026-11-20; native CPU/V100 partition selection
Source: deepmodeling/abacus-develop; branch LTS; commit 0d967f1f1bc39c16e3bf435e5f02281c72daaf05 (2026-11-16)
Features: MPI/OpenMP; PW/LCAO; HSE (LibRI); DeePKS (LibTorch 2.1.2); LibXC; ELPA; CUDA sm70 and CAL/cuSOLVERMp on GPU partitions
Toolchain: OpenMPI 5.0.10 / NVHPC 26.3 GNU; CPU GCC 13.3.0; CUDA 12.9.1; ELPA 2026.02.001; GPU CAL/cuSOLVERMp from NVHPC 25.7
CPU SIMD: DSPRHBM/4V100 AVX512; 16V100 AVX2; 8V100V0 AVX512 with AVX2 dependency libraries; GPU V100 sm70
```

登录节点尝试加载时先显示帮助，再要求进入 Slurm 作业。Lmod 缓存未刷新时使用 `module --ignore_cache avail abacus`。

## 验证范围

原始 SIF 均通过版本、CMake 功能选项、动态库解析和单 MPI 进程的上游算例检查；最大能量偏差低于 `2e-11 eV`。GPU 算例还检查实际设备与 cuSOLVERMp 执行记录。HSE、DeePKS 此轮使用 CPU 路径；这些结果不构成多卡、多节点或性能验收。

`--verify` 验证安装完整性，不提交计算任务。`prepare-release.py` 供维护者准备交付包。`validate-staged.py` 和 `staged-entry.sh` 供维护者在项目暂存树上检查精简安装及 module，均不安装到 `/opt`。

暂存试装和 module 验证结果见 [VALIDATION.md](VALIDATION.md)。三个分区的暂存运行复验通过，4V100 的额外暂存计算仍在排队；其原始 SIF 科学验收已通过。
