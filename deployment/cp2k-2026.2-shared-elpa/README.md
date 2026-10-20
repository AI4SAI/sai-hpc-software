# CP2K 2026.2：共享 ELPA 版本上线

本包安装四分区已验收的 release `v2026.2-g67b5da876dd6-raed7aa53ff43`，
然后更新公共 `cp2k/2026.2-auto`。四分区 SIF 和原生导出已通过检查，
共 56 项单/双节点科学计算通过；[验收记录](../../docs/evidence/cp2k-shared-elpa-release-20261020/README.md)。

2026-10-20 管理员已完成安装和公共 module 更新。`stardust` 对物理文件、
权限、共享依赖及实际运行完成复验：四分区作业 `1879431`–`1879434`
均 `COMPLETED 0:0`，全部 **56 项物理环境计算通过**，原始日志独立复核通过。
[安装后验收与复核脚本](../../docs/evidence/cp2k-shared-elpa-native-20261020/README.md)。
已安装环境可直接在 Slurm 作业内加载 `cp2k/2026.2-auto`。

## 管理员执行

```bash
cd /home/stardust/sai-hpc-software/deployment/cp2k-2026.2-shared-elpa
bash install.sh --install
bash install.sh --verify
```

执行账号需要对 `/opt/apps/cp2k` 和 `/opt/modules/modulefiles/apps/cp2k` 有写权限。
`--install` 已包含完整预检，不必先重复执行 `--check`。只想预览时运行
`bash install.sh --check`，该命令只读。

预检逐文件读取待安装运行文件，核验 SHA256、链接、固定前缀、目标冲突和共享 ELPA。
准备包时已完整核验 SIF；管理员默认直接核验有固定哈希的 `.native` 文件，
不再重复扫描 SIF。需要额外核验原镜像可加 `--check-images`。
脚本按分区输出进度和耗时，无需 Apptainer、编译器或 GPU。

四个目录全部复制并回读校验通过，才原子切换公共 module。容器内外路径保持：

```text
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/4V100
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM
```

安装保留 CP2K 可执行文件、用户工具、必要私有运行库、完整原始 `share/cp2k/data`
及精简来源记录。剔除测试程序、开发文件和构建探针，不增加 examples/benchmark。
共享 ELPA 保持在已验收的 `/opt/devtools/elpa/...`，不复制或替换它。
`release.json` 列出各分区文件数、字节数及来源哈希；`<分区>-receipt.json` 给出
逐文件安装与剔除清单。仅使用家目录中的导出，暂存目录建立在目标 `/opt` 文件系统内。

旧 CP2K 目录保留。只接受已知旧 module 或本次新 module；其他修改会停止安装。
旧 module 自动备份到 `/opt/modules/modulefiles/apps/cp2k/.2026.2-auto.before-<旧哈希前16位>`。
已加载旧 module 的 shell 仍可正常卸载，再加载新版。运行中的旧作业继续使用旧前缀。
安装失败会保留尚未发布的 `.分区.incoming-<PID>` 以便检查；完整的新分区可重复安装并跳过。
不会删除原 SIF、原生导出或旧安装，也不改 `.version` / `.modulerc`。

如需回退公共入口，管理员把本包 `previous-2026.2-auto` 安装回原 module 路径即可；
它固定为先前已验收的版本，旧软件目录始终保留。回退后应在新 shell 中重新加载。

```bash
install -m 0644 previous-2026.2-auto /opt/modules/modulefiles/apps/cp2k/2026.2-auto
```

## 安装后原生验收

管理员完成安装后，由 `stardust` 普通账号执行：

```bash
bash /home/stardust/sai-hpc-software/deployment/cp2k-2026.2-shared-elpa/submit-native-checks.sh all
```

四分区各提交一个双节点作业，使用新 SIF 的原始七组输入，分别执行单/双节点计算，
共 56 项；能量、力、版本、rank 分布及实际动态库加载都与新发布身份核对。
ELPA 必须从记录的共享安装加载，HCOLL 必须来自真实 HPCX 路径。
结果与 MPI 临时目录均位于执行账号的家目录，不使用计算节点 `/tmp`。
`--verify` 只证明安装文件完整，物理运行验收由上述作业完成。

用户在 Slurm 作业内使用原入口：

```bash
module use /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
mpirun ... cp2k.psmp -i input.inp
```

## 维护者复现准备

在集群开发 checkout 执行 `python3 -B deployment/cp2k-2026.2-shared-elpa/prepare.py`。
输出目录必须尚不存在；脚本固定已验收镜像身份，核验共享依赖、原生文件与科学参考，
生成独立管理员包。它复用旧发布的精简/复制逻辑，并把实现复制为 `base_install.py`，
其哈希固定在发布清单中。管理员无需读取开发 checkout。
跨账号执行只需源材料的读取/目录遍历权限，不授予写入 stardust 文件的权限。
