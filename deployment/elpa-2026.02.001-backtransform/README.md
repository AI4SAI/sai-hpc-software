# ELPA2 GPU 回变换替换包

两套 NVIDIA 库已在容器内编译完成并由管理员安装，独立数值、Fortran 消费程序和 communicator
生命周期检查通过。16V100、4V100、8V100V0 的全部 **42 项 CP2K 测试**也通过，
输入和验收容差保持原样。管理员部署包已经生成，`--check` 通过；
[release.json](release.json) 和逐文件清单固定本次构建与验证证据。公共 module 下的
四分区物理复验 `1869223`–`1869226` 全部通过，共 460 次数值求解，包含 GCC 14.3
消费者及 communicator 释放检查。[物理验收记录](../../docs/evidence/elpa-backtransform-native-20261020/README.md)。

共享 ELPA 的两阶段 GPU 路径在 CP2K 64 水分子输入中发散，小矩阵多节点测试
出现 `cudaHostRegister` 错误。候选补丁使两次特征向量回变换采用上游已有的
主机缓冲通信路径，保留 GPU 计算、cuSOLVER、多 CUDA 架构以及其他 CUDA-aware
MPI 路径。它是经过指定算例验证的本地兼容方案，不宣称是上游完整修复或性能提升。
[诊断、补丁、构建和实验脚本](../../docs/evidence/cp2k-export-recovery-20261020/README.md)。

容器内编译和物理安装路径相同：

- AVX2：`/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia`
- AVX512：`/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia`

安装器只处理这两个前缀，沿用已验收的备份和目录切换实现。CPU 库及 36 行公共
module 无需替换。只接受已知旧清单或本次新清单，拒绝覆盖其他修改；备份保留在
各前缀旁的 `.nvidia.before-<旧清单哈希前16位>`，不删除旧树。

在生成的集群家目录部署包中，由管理员执行：

```bash
cd /home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-backtransform
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

载荷分别为 AVX2 19.31 MiB、AVX512 20.44 MiB。安装入口禁用 Python 字节码缓存，
检查时只需读取包；跨账号权限按 `saiuser01` 的有效 POSIX ACL 核对，保留读/遍历权限，
不增加写权限。构建、载荷和验收日志均保存在 stardust 家目录。

安装应在相关 ELPA 作业结束后切换。本次已完成物理复验，并更新 CP2K 的
共享依赖锁；接下来生成和验收新身份的 CP2K 产物。隔离候选库的 CP2K 测试结果不能
直接标记为旧依赖锁下的正式发布；216 水分子调优仍需等待正式验收。

在集群开发 checkout 中复现原生验收：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 deployment/elpa-2026.02.001-backtransform/native-checks.py --submit
# 四个作业结束后，使用上一步打印的结果目录：
PYTHONDONTWRITEBYTECODE=1 python3 deployment/elpa-2026.02.001-backtransform/native-checks.py --verify <结果目录>
```

提交时冻结脚本、库清单、公共 module 和数值探针，运行前后在计算节点核对物理库。
验证器独立解析原始数值、资源释放和动态加载记录；`--offline` 支持下载后的再次核对。
