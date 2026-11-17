# ABACUS beta13 安装后验收

源码 `v3.11.0-beta13@fcd57f6db2660a2d82bd66dfbc93d78e802600c5`，安装 module 为 `abacus/3.11.0-beta13-auto`。2026-11-17 已核对四分区物理安装的完整文件、权限、链接和 module，均通过 `ABACUS_NATIVE_VERIFIED`。登录节点的 module 帮助及先显示帮助再拒绝加载的行为也已验证。

## 原生计算状态

以下为 2026-11-17 晚间的状态快照，未将排队作业计为验收通过。

| 分区 | 作业 | 状态 | 算例 | 最大参考能量误差 |
| --- | --- | --- | --- | --- |
| 16V100 | 2542696 | COMPLETED 0:0，原生验收通过 | 8 | 2.05e-8 eV |
| DSPRHBM | 2542698 | COMPLETED 0:0，原生验收通过 | 6 | 1.54e-9 eV |
| 4V100 | 2542695 | 排队，等待 GPU 资源 | 待运行 | 待验证 |
| 8V100V0 | 2542697 | 排队，等待 GPU 资源 | 待运行 | 待验证 |

16V100 使用 `16v100n17/18`，DSPRHBM 使用 `d9470n03/04`。每个分区都要求版本/功能信息、每 rank 的实际程序哈希及动态库来源正确。PW、HSE、DeePKS 分别运行单节点和双节点；GPU 分区另跑双节点 cuSOLVERMp 和 NCCL 用例。验收阈值为 `1e-5 eV`，单节点与双节点结果也必须一致。

已通过的作业直接使用物理 `/opt` 中的 ABACUS 和宿主 MPI，GPU 未隐藏；16V100 的 GPU 求解和 NCCL 通信记录通过检查。测试文件不进入应用安装目录。

## 证据与自动收尾

结果根目录：

```text
/org/org-open-sai/proj-sai-hpc-software/runtime-tests/abacus-beta13-physical-20261117-184206-398203
```

各分区目录保存冻结的控制脚本和算例、Slurm 分配、每个 rank 的 `ldd` 与哈希、计算原始输出和 `native-acceptance.json`。`completed-native-summary.json` 保存上表两个已完成分区的数值摘要。

等待中的两个 GPU 作业已将时限从 40 分钟缩短到 10 分钟以便调度；原测试内容和资源需求未改动，调整记录在 `pending-time-limit-adjustment.json`。当时可调度 GPU 均被占用，8V100V0 空闲的四个节点属于其他账号的预约。

收尾作业 **2542952** 以 `afterany` 依赖四个验收作业，完成后使用冻结的验证器重读原始结果和 Slurm 状态，写入根目录的 **`final-status.json`**。后续应以该文件及原始日志为准；本快照不表示四分区原生运行已全部通过。

复验命令：

```bash
python3 /home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta13/native_checks.py verify \
  /org/org-open-sai/proj-sai-hpc-software/runtime-tests/abacus-beta13-physical-20261117-184206-398203
```

验证器回归测试：30 项通过，2 项因 VM 缺少 Tcl 跳过；真实 Lmod 行为已在集群核查。
