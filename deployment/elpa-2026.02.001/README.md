# ELPA 2026.02.001: AVX2 / AVX512 容器构建与管理员部署

当前统一入口和复现流程见 [ELPA 文档](../../docs/ELPA.md)。10 月 19 日后续审计确认
物理共享 GPU 版仍缺少上游 MPI communicator 回收修复。
当前构建脚本已补上该修复，新三套 SIF 和四分区复验均已通过，
[管理员替换包](../elpa-2026.02.001-commfix/README.md) 已准备。
本目录原发布清单保留 10 月 18 日的历史身份；替换须使用新包，不能修改旧清单冒充新验收。

2026-10-18：两套 SIF 均已通过 48 次数值求解及 C/Fortran、module 检查。
AVX2 构建任务为 `1846755`，AVX512 为 `1846756`，均 `COMPLETED 0:0`。
管理员部署包位于 `/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001`，
`--check` 已通过，约需复制 19.43 MiB + 20.56 MiB。
[验收记录](https://github.com/AI4SAI/sai-hpc-software/blob/fix/workflow-closure/docs/evidence/elpa-site-20261018/README.md)；SIF 和清单校验值见 `release.json`。

管理员已完成物理安装。`stardust` 在 16V100、4V100、8V100V0 上的任务
`1847651`、`1847652`、`1847653` 各通过 48 次 CPU/GPU 求解，文件清单、module、
C/Fortran 调用及真实进程的库加载路径均通过检查，直接执行物理 `/opt`，不使用容器。
DSPRHBM 的主机数组接口也通过 24 次 CPU 求解（`1847682`），但
`elpa_eigenvectors_double` / `elpa_eigenvectors_double_complex` 在无 CUDA 驱动的节点
会因自动检测设备指针而退出（`1847654`）。当时尚未通过纯 CPU 节点的显式 C 接口验收。
详见[物理验收及接口限制](../../docs/evidence/elpa-site-20261018/README.md#physical-installation-acceptance)。

2026-10-19：已为此限制构建并验收[纯 CPU 补充包及分区 module](../elpa-2026.02.001-cpu/README.md)。
管理员已安装到 `-gnu-avx512/cpu` 并升级现有 `elpa/2026.02.001-2603-gnu-auto`。
物理任务 `1861947`–`1861950` 已通过四分区 168 次求解，包括 CPU 原始显式接口的 24 次。
正式 module 按分区选择库，
DSPRHBM 使用 CPU 版，其余三个 V100 分区使用相应 GPU 版。下文和本目录脚本保留
最初 GPU 部署的验收状态；升级后请使用补充包的安装器核对物理文件。

沿用集群 `elpa/2026.02.001-2603-gnu` 的上游源码、GCC/GFortran 13.3.0、
OpenMPI 5.0.10、CUDA 12.9.1 / NVHPC 26.3、OpenBLAS 0.3.32 和 ScaLAPACK 2.2.3。
保留 OpenMP、cuSOLVER、CUDA-aware MPI、CUB、SM80 专用内核，GPU 目标仍为
`sm_70,sm_75,sm_80,sm_86,sm_89`。

参考脚本来自
`/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain/scripts/stage3/install_elpa.sh`。
源码 SHA256：`a379f27f4dbd27b2ee45017afec656d064301e97150c874649bdfd64957b75ed`。
最初 10 月 18 日构建只应用 `cuda-aware-cpu.patch`：ELPA2 的 tridi-to-band、band-to-full 阶段在 CPU 求解时
使用主机缓冲区做 MPI 广播和归约，修复启用 CUDA-aware MPI 后误用未初始化设备指针的错误。
GPU 分支和 CUDA-aware 功能保留。补丁随安装元数据保存。
站点 OpenMPI 的 libnbc 在多 rank 的 GPU 缓冲区归约上崩溃；补丁还把这两个
GPU 归约和一个 GPU 广播中“非阻塞调用后立即等待”的组合替换为等价的阻塞 collective。
仍直接使用设备缓冲区；CPU 的非阻塞分支保留。此兼容处理随同数值测试覆盖。

## 安装位置

容器内配置、安装、验收和之后物理部署使用完全相同的前缀：

```text
/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia
/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia
```

新增 module：`elpa/2026.02.001-2603-gnu-auto`。
旧 `elpa/2026.02.001-2603-gnu` 不变。

AVX2 版使用 `-march=x86-64-v3 -mtune=znver3 -mno-avx512f`，关闭 AVX512 内核；
AVX512 版使用 `-march=x86-64-v4 -mtune=znver4`，保留 AVX2/AVX512 内核。
公共参数仍为 `-O2 -g -fno-omit-frame-pointer -fopenmp -fno-lto`。
两套分别使用同 ISA 的系统 MPI/BLAS。

auto module 的 `avx512_vnni` 检测与现有 MPI/BLAS 的检测保持一致。
因此 16V100 和 Gold 6146 的 8V100V0 使用 AVX2 版；4V100 和 DSPRHBM 使用 AVX512 版。
请在实际计算或编译节点加载 module，以免继承登录节点选择的另一套路径。

## 构建和验收

从集群开发 checkout 执行 `bash deployment/elpa-2026.02.001/submit.sh`，分别提交到
16V100、4V100。CPU 数量由站点 GPU 策略分配，并行度读取实际 CPU affinity。
所有源码、对象、临时文件和 Apptainer 缓存均在 stardust 家目录的独立 run 中；
容器内 `/tmp`、`/var/tmp` 绑定到该 run 的家目录，未使用宿主机 `/tmp` 存储。
系统工具和依赖只读绑定，安装目录以预期 `/opt` 前缀可写绑定进容器。

ELPA 原版 `make all` 会在关闭测试程序后仍构建测试辅助库，并存在 `elpa.mod`
生成顺序问题；脚本显式先编译主库及内核查询工具，安装时排除未安装的测试目标。
验收从最终 SIF 编译独立 C 调用程序，检查 128 阶解析 Hermitian 矩阵：
单/双 MPI 进程、CPU/GPU、ELPA1/ELPA2、实数/复数、每种重复求解两次。
另覆盖 ELPA2 的非阻塞 MPI 分支；每套 48 次求解，验证特征值、残差和正交性均小于 `1e-10`。
同时编译运行 Fortran 调用程序，验证安装的 `.mod` 文件可以正常使用。
另检查动态链接、CPU 内核列表以及五种 CUDA 架构。V100 上不验证 SM80 专用内核的执行性能。

MPI libtool 中的 `/opt/mellanox/hcoll` 仅作为构建兼容挂载；交付库的 RUNPATH 和
ELPA `.la` 使用实际 HPCX HCOLL 目录，验收不提供旧别名挂载。
补齐上游 `.pc` 遗漏的 CUDA `-L`/RUNPATH，确保新程序能直接通过 pkg-config 链接。

## 管理员操作

两套通过后，`prepare-release.py --avx2 <run> --avx512 <run> --output <新目录>`
从最终 SIF 提取两套安装树，生成 SHA256 清单及数值验收记录。
部署包保留动态库、C 头文件、Fortran 模块、pkg-config/libtool 元数据、内核查询工具、
上游文档和构建来源记录；不搬源码树、对象、测试可执行文件和缓存，也不复制系统依赖。

管理员在该部署包目录执行：

```bash
cd /home/stardust/sai-hpc-software/deployment/elpa-2026.02.001
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

`--check` 默认只读核对文件及目标冲突；`--install` 只写上面的两个新安装目录和新增
module，不覆盖不同内容的已有安装。无需管理员启动容器或执行其中的代码。
`--verify` 核对物理文件；安装后的计算节点数值验收仍须另行进行。

## 物理运行复验

升级后的安装使用 CPU 补充包的入口，从开发 checkout 执行：

```bash
bash deployment/elpa-2026.02.001-cpu/submit-native-checks.sh all
```

结果和 MPI/CUDA 临时文件均落在执行账号的家目录，默认验证原始显式 C 接口。
完整命令见[CPU 补充包的物理复验说明](../elpa-2026.02.001-cpu/README.md#物理运行复验)。
本目录旧提交入口及原始发布清单保留最初 GPU 部署身份，不用于升级后的物理安装。

历史主机数组诊断使用 `elpa_eigenvectors_a_h_a_d` / `elpa_eigenvectors_a_h_a_dc`，
其原始日志仍可用以下命令独立校验；新 CPU 验收无需该别名替换：

```bash
python3 verify-native-results.py <GPU结果目录> --partitions 16V100 4V100 8V100V0
python3 verify-native-results.py <CPU诊断结果目录> --partitions DSPRHBM --api host
```

记录同时固定被验收的分区和 C 接口，不把未通过的接口或分区计入成功。
