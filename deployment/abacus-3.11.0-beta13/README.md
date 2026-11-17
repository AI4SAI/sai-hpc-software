# ABACUS 3.11.0-beta13 管理员安装

安装已验收的 `v3.11.0-beta13@fcd57f6db2660a2d82bd66dfbc93d78e802600c5` 四分区产物，无需重新编译。由有目标目录写权限的 `saiuser01` 执行：

```bash
cd /home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta13
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

`--check` 为只读预检，`--install` 已包含预检和安装后逐文件校验，`--verify` 独立复验已安装文件。成功分别输出 `ABACUS_NATIVE_PREFLIGHT_PASSED`、`ABACUS_NATIVE_INSTALLED`、`ABACUS_NATIVE_VERIFIED`。相同内容可重复安装；已有不同文件或 module 会阻止覆盖。

交付包在家目录保留约 **703 MiB** 的四份压缩 SIF 及校验清单。管理员无需读取受限的项目目录。安装脚本核对源码、分区、SIF SHA256、科学验收记录和运行文件清单，再从镜像读取文件内容。每分区在目标文件系统暂存并逐文件验证后更名，最后发布 module。

## 安装目录

```text
/opt/apps/abacus/abacus-prerelease-v3.11.0-beta13-gfcd57f6db266-r4555f0f84db5/
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
│   │   └── lib/
│   └── nep/lib/libnep.so
└── share/licenses/

/opt/modules/modulefiles/apps/abacus/3.11.0-beta13-auto
```

每分区 19 个普通文件及 1 个相对链接，四分区共约 **1.84 GiB**。上游将 beta13 标记为 prerelease，因此使用产物原有的 `abacus-prerelease-...` 路径，保持其编译时前缀。beta12 的独立安装及 module 保留。

| 分区 | 安装文件大小 |
| --- | ---: |
| 16V100 | 472.86 MiB |
| 4V100 | 473.50 MiB |
| 8V100V0 | 472.74 MiB |
| DSPRHBM | 463.64 MiB |

MPI、ELPA、LibXC、FFTW、SAIBLAS、CUDA、cuSOLVERMp/NCCL 使用已验收的站点依赖。源码、头文件、静态开发库、自写测试、工作流元数据和 SIF 不安装进应用目录。精确路径、权限、字节数和哈希记录在 `runtime-plan.json`。

## 用户加载

在对应分区的 Slurm 作业内：

```bash
module load abacus/3.11.0-beta13-auto
mpirun ... abacus
```

module 根据分区选择对应的程序和依赖，显示版本、源码摘要及安装路径。用户直接运行物理 `/opt` 中的程序，运行时无需家目录或 Apptainer。登录节点可以执行 `module help abacus/3.11.0-beta13-auto`；尝试加载时先显示帮助再拒绝加载。若 module 列表未刷新，使用 `module --ignore_cache avail abacus` 查询。

## 验证

四份 SIF 来自 [2026-11-14 自动构建](https://github.com/AI4SAI/sai-hpc-software/actions/runs/43487977288)，均已通过多节点 MPI、PW、HSE 和 DeePKS 验收；GPU 分区还通过 cuSOLVERMp/NCCL 检查。`--verify` 核对物理安装文件的完整性，安装后的原生计算验收另行执行。

`prepare-release.py` 供维护者从固定的已验收产物生成交付包；管理员直接运行 `install.sh`。

## 安装后的原生计算验收

维护者可在安装后运行 `bash submit-native-checks.sh all`，也可以将 `all` 替换为一个分区名。作业使用项目账号 `proj-sai-hpc-software`，工作目录、结果和临时文件均在 `/org/org-open-sai/proj-sai-hpc-software`。

验收使用已固定的 beta13 SIF 算例和原始输出，直接运行物理安装的程序。每分区检查版本、功能开关、每个 MPI rank 的程序哈希与动态库路径，以及 PW、HSE、DeePKS 的单节点和双节点计算；GPU 分区额外检查 cuSOLVERMp 求解及 NCCL 集合通信的实际执行。能量误差阈值为 `1e-5 eV`，也核对单节点与双节点结果一致性。

提交脚本会输出结果目录。作业结束后，用 `python3 native_checks.py verify <结果目录>` 重新读取原始记录并核对 Slurm 退出状态。`finalize-native.sbatch` 可作为依赖这些验收作业的收尾任务，从冻结的控制脚本验证结果并写入 `final-status.json`。

本次安装验收记录见 [2026-11-17 状态](../../docs/evidence/abacus-beta13-installed-20261117/README.md)。
