# ELPA2 GPU 回变换候选修复

此目录提供两套 NVIDIA 库的替换工具。只有新 SIF 的独立数值、communicator
生命周期检查和三个 GPU 分区各 14 项 CP2K 验收全部通过，才能生成部署载荷。
实际资格以生成包的 `release.json` 和逐文件清单为准。

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
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

应在相关 ELPA 作业结束后切换。随后由 stardust 复验物理安装，更新 CP2K 的
共享依赖锁，再生成和验收新身份的 CP2K 产物。隔离候选库的 CP2K 测试结果不能
直接标记为旧依赖锁下的正式发布；216 水分子调优仍需等待正式验收。
