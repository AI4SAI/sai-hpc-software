# ELPA 2026.02.001 communicator 修复替换包

2026-10-19：三套最终 SIF 和四分区容器复验均已通过，管理员包 `--check` 通过。
AVX2 GPU 19.44 MiB、AVX512 GPU 20.57 MiB、CPU 12.82 MiB；物理替换尚未执行。
[构建、数值和资源释放验收记录](../../docs/evidence/elpa-commfix-20261019/README.md)。

将上游 [PR #85](https://github.com/marekandreas/elpa/pull/85) 的修复
`20be982db53a671b9aac866a726aebe153a766e6` 回补到官方 2026.02.001。
沿用此前的 GCC 13.3、MPI/BLAS、CUDA、多架构内核和 CUDA-aware MPI 兼容补丁。
仍在容器内直接编译到最终 `/opt` 前缀，所有可写数据位于集群家目录。

| 构建 | 使用分区 | 容器内和物理机上的前缀 |
| --- | --- | --- |
| AVX2 / NVIDIA | 16V100、8V100V0 | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia` |
| AVX512 / NVIDIA | 4V100 | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia` |
| AVX512 / CPU | DSPRHBM | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu` |

公共入口继续使用 `elpa/2026.02.001-2603-gnu-auto`。安装器核对现有 module 的哈希，
直接替换它选择的三个软件目录。

## 生成替换包

```bash
bash deployment/elpa-2026.02.001/submit.sh <唯一标识>
bash deployment/elpa-2026.02.001/submit-cpu.sh <同一标识>
bash deployment/elpa-2026.02.001-commfix/submit-checks.sh <AVX2运行目录> <AVX512运行目录> <CPU运行目录>
```

每套最终 SIF 先通过原有 C/Fortran、ELPA1/2、实数/复数、单/双 MPI rank 数值测试，
再创建和销毁 32 个 ELPA 对象，每个对象求解两次。
GPU 路径要求每个 rank 创建 32 个临时 communicator、释放 32 个、剩余 0 个；
CPU 路径要求该类 communicator 为 0。缺少跟踪输出也会判失败。
三套 SIF 还需在四个实际分区通过 module 选择及运行复验。

`prepare-release.py` 从最终 SIF 导出库、头文件、Fortran module、pkg-config、必要工具、
上游文档和构建元数据；保留原始验收日志及 SHA256 清单。源码树、对象、缓存和系统依赖
不放入载荷。发布清单中的作业号、SIF 哈希和测试结果是具体部署包的验收依据。

```bash
python3 deployment/elpa-2026.02.001-commfix/prepare-release.py \
  --avx2 <AVX2运行目录> --avx512 <AVX512运行目录> --dsprhbm <CPU运行目录> \
  --checks <四分区结果目录> --output <新部署目录绝对路径>
```

## 管理员替换

在生成的 `/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-commfix` 中执行：

```bash
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

`--check` 只读核对三个载荷、现有安装和 module。只接受此前验收版本或本次修复版本，
拒绝覆盖管理员另外改过的文件。`--install` 先复制、验证暂存目录，再将旧目录改名备份，
最后切入新目录；不在原 `.so` 文件上写入。切换失败会立即恢复该目录。
备份位于同一父目录下的 `.nvidia.before-<旧清单哈希前16位>` 或 `.cpu.before-<哈希>`，
安装器打印完整位置，不删除备份。重复执行会跳过已更新且内容一致的目录。
三套目录依次替换；中断后重新执行即可完成剩余目录。

替换应安排在相关 ELPA 使用任务结束后，避免同一任务后续启动的进程混用两套库。
安装完成后由 stardust 在四分区执行物理环境数值和 communicator 生命周期复验。
现有 CP2K 共享依赖锁固定旧库哈希，须根据已安装且复验通过的新文件更新后再生成候选；
不能把此前旧哈希下的验收记录直接当作修复后的验收。
