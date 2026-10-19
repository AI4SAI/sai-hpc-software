# ELPA 2026.02.001：构建、部署和复现

当前公共入口是 `elpa/2026.02.001-2603-gnu-auto`。管理员已经安装三个库，
`stardust` 已在四个分区完成物理环境验收。源码、补丁、构建/部署脚本、安装文件清单
和原始验收日志均在本仓库；大体积源码压缩包、SIF 和构建缓存保留在集群家目录。

后续 GPU 生命周期审计发现此安装仍缺少上游 MPI communicator 回收修复。
下述数值验收成立，但长时间反复 GPU 对角化需要先修复并复验，见文末限制。

## 分区选择和固定路径

| 分区 | ISA / 后端 | 容器内和物理机上的相同安装前缀 |
|---|---|---|
| 16V100、8V100V0 | AVX2 / NVIDIA | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia` |
| 4V100 | AVX512 / NVIDIA | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia` |
| DSPRHBM | AVX512 / CPU | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu` |

8V100V0 的 Gold 6146 有 AVX512，但没有站点 AVX512 MPI/BLAS 所需的 VNNI，
所以选 AVX2 依赖。DSPRHBM 的 CPU 库在编译时完全移除 CUDA，原始显式 C 接口
不会为了判断指针类型而初始化 CUDA 驱动。

请在实际 Slurm 分配的计算节点加载 module。该 module 按 `SLURM_JOB_PARTITION`
选择，并核对 CPU ISA、MPI 和 BLAS；登录节点上加载会明确报错。查看不需要分配：

```bash
module show elpa/2026.02.001-2603-gnu-auto
# 以下在 Slurm 分配内执行
module load elpa/2026.02.001-2603-gnu-auto
printf '%s\n' "$ELPA_ROOT"
pkg-config --modversion elpa_openmp
```

## 保留的站点方案与修改

源码 `elpa-2026.02.001.tar.gz` 的 SHA256 为
`a379f27f4dbd27b2ee45017afec656d064301e97150c874649bdfd64957b75ed`。
参考站点 ABACUS toolchain 的 `scripts/stage3/install_elpa.sh`；提交脚本将参考脚本、
原始 module 和源码归档到每次 run 的 `input/`，并保存哈希。

沿用 GCC/GFortran 13.3.0、OpenMPI 5.0.10、OpenBLAS 0.3.32、ScaLAPACK 2.2.3、
OpenMP 和共享库。GPU 版沿用 CUDA 12.9.1 / NVHPC 26.3、cuSOLVER、CUB、
CUDA-aware MPI、SM80 专用内核以及 `sm_70,sm_75,sm_80,sm_86,sm_89`。
完整 configure 参数和编译器选项随每套安装保存在 `share/sai/`。

- AVX2：`-march=x86-64-v3 -mtune=znver3 -mno-avx512f`，关闭 AVX512 内核。
- AVX512 GPU：`-march=x86-64-v4 -mtune=znver4`。
- AVX512 CPU：`-march=x86-64-v4 -mtune=sapphirerapids`，禁用 GPU、cuSOLVER、CUB 和 CUDA-aware MPI。
- [兼容补丁](../deployment/elpa-2026.02.001/cuda-aware-cpu.patch) 修复 CPU 求解误用设备缓冲区，
  并将三个 GPU collective 的“非阻塞后立即等待”改成阻塞调用，绕开站点 OpenMPI libnbc 崩溃。
- 交付库保留实际 HPCX HCOLL 路径；`/opt/mellanox/hcoll` 只用于编译兼容。
  pkg-config 补齐 CUDA 的链接路径，便于下游直接编译。

## 从仓库重新构建

在集群 checkout `/home/stardust/sai-hpc-software/development/workflow-closure` 执行：

```bash
bash deployment/elpa-2026.02.001/submit.sh
bash deployment/elpa-2026.02.001/submit-cpu.sh
```

前者提交 16V100/4V100 两套 GPU 构建，后者提交 DSPRHBM CPU 构建。
每次提交冻结控制脚本、源码和参考输入，输出作业号和独立 run 路径。
依赖的预置源码位置是：

```text
/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain/build/elpa-2026.02.001.tar.gz
```

构建需要已有站点 MPI/BLAS/CUDA、Apptainer 1.4.4 和项目的 `minimal-v1.sif`。
脚本核对源码 SHA256；若预置归档已被清理，先恢复相同哈希的上游归档。
所有可写数据落在 `/home/stardust/sai-hpc-software/runs/elpa-*`；容器 `/tmp` 和
`/var/tmp` 也绑定到这些 run 的家目录存储。依赖只读，安装直接使用表中的最终前缀。

GPU 脚本和最初发布清单见 [GPU 部署包](../deployment/elpa-2026.02.001/README.md)，
CPU 版及当前分区 module 的安装流程见 [CPU 补充包](../deployment/elpa-2026.02.001-cpu/README.md)。
构建后通过最终 SIF 编译独立 C/Fortran 消费程序并运行数值验收，再由 `prepare-release.py`
生成给管理员的部署包。安装器只搬库、头文件、Fortran module、pkg-config、必要工具和来源记录；
不搬源码树、对象和缓存。重新编译的字节哈希可能变化，应生成新的发布清单并重新验收。

## 已完成的验收和复验入口

当前物理验收作业 `1861947`–`1861950` 全部 `COMPLETED 0:0`，共 **168 次求解**：
GPU 分区各 48 次，DSPRHBM 24 次。覆盖原始显式 C 接口、实数/复数、ELPA1/2、
1/2 MPI rank、重复求解及非阻塞选项，并验证 Fortran 消费程序、文件清单、module
加载/卸载以及实际 ELPA/MPI/BLAS/HCOLL 库路径。
最大特征值、残差、正交性误差为 `2.22e-15`、`6.46e-16`、`1.665e-15`。
这些结果不代表大体系或跨节点性能测试。

```bash
bash deployment/elpa-2026.02.001-cpu/submit-native-checks.sh all
```

[物理验收报告与 205 份原始记录](evidence/elpa-auto-native-20261019/README.md) 是当前验收依据；
[最初 GPU 构建记录](evidence/elpa-site-20261018/README.md)、
[CPU 补充记录](evidence/elpa-cpu-20261019/README.md) 和
[module 升级记录](evidence/elpa-auto-20261019/README.md) 保留完整历史。

## CP2K 使用此库

已经部署的 CP2K 2026.2 仍对应原先验收过的私有 ELPA 2026.02.002。
新配方改用表中的共享 ELPA 2026.02.001，并继续使用 **GCC 14.3.0** 编译 CP2K。
两者是不同候选，不能通过修改旧产物记录来完成迁移。

[依赖锁](../controller/cp2k_site_elpa.json) 固定每套库、头文件、Fortran module、
pkg-config、补丁和构建参数的哈希，来源与上述发布清单逐项对应。
构建容器按冻结 target 选择同一分区映射，不依赖容器外继承的 module 状态；
先编译运行 [GCC 14 Fortran 数值探针](../controller/cp2k_elpa_abi.f90)，再配置 CP2K。
最终 CMake、`ldd` 和安装验证均要求选中同一套库。共享 ELPA 保留在原 `/opt/devtools`
路径，不复制到 CP2K 安装树。原始 CP2K data 和其他必要运行依赖按现有规则打包。

四分区跨编译器探针 `1862298`–`1862301` 已通过：使用 GCC 14.3 编译 Fortran 消费程序，
读取 ELPA 13.3 的 `.mod`，调用 type-bound API 求解 32 阶矩阵，并核对解析特征值和残差。
最大误差分别为 `1.33e-15`、`8.88e-16`。
[原始日志和哈希](evidence/cp2k-shared-elpa-20261019/preflight.json) 只证明这个接口兼容性，
新 CP2K 应用的科学验收仍须随完整构建执行。
另有 `1862389`、`1862390` 验证正式构建使用的 fakeroot/隔离网络和 CMake 查找，
分别在 16V100、DSPRHBM 通过；[完整证据](evidence/cp2k-shared-elpa-20261019/cmake-preflight.json)。

新配方需要新的 recipe 身份、容器内/外相同的 `/opt/apps/cp2k/<构建名>/<分区>`
前缀，以及独立科学验收。手动触发四分区 release 构建：

```bash
gh workflow run cp2k.yml --repo AI4SAI/sai-hpc-software --ref fix/workflow-closure \
  -f tracks=release -f targets=16v100-avx2,4v100-avx512,8v100v0-avx512,dsprhbm \
  -F retry_releases=true
```

release 与 development 各自解析源码并保持独立记录；`tracks=development` 可构建最新分支。
使用共享 ELPA 的候选仍须通过 PBE OT、普通对角化、强制 ELPA、DFT-D4、TBLITE、
较大水体系及单/双节点测试。issue #9 的 direct generalized diagonalization 是另行测量的
运行时选项，详见 [V100 调优记录](CP2K-V100-TUNING.md#shared-elpa-rebuild-and-issue-9)。

## GPU 求解器生命周期限制

作业 `1862456` 在 16V100 对同一物理 GPU 库分别执行 CPU/GPU 求解。
每个进程创建和销毁 8 个 ELPA 对象，每对象求解两次，32 次数值结果全部通过。
跟踪 ELPA 调用的 Fortran `MPI_Comm_split_type` / `MPI_Comm_free` 后，CPU 路径
未创建这类临时 communicator；GPU 路径创建 8 个、显式释放 0 个，进程退出前仍有 8 个。

源码 `src/GPU/check_for_gpu_template.F90` 创建局部 communicator 读取每节点 rank 数，
随后没有释放。CP2K 2026.2 在每次 ELPA 对角化中创建/销毁对象，长时间反复调用
会积累这种资源。[上游 PR #85](https://github.com/marekandreas/elpa/pull/85)
已提供回收修复；旧 CP2K 私有 ELPA 配方包含该修复，此次沿用站点方案的共享库未包含。

[复现代码、原始日志及适用于 2026.02.001 的补丁](evidence/cp2k-shared-elpa-20261019/communicator-audit.json)
已存档。补丁尚未应用到管理员安装的共享库。本轮 CP2K 编译用于验证共享依赖集成；
将新候选用于长时间 GPU 对角化前，应构建修复后的共享 ELPA、复验对象生命周期，
并以新文件哈希更新 CP2K 依赖锁和候选身份。不要原地替换库后继续使用旧验收记录。
