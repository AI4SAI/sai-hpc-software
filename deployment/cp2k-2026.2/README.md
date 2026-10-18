# CP2K 2026.2 物理发布

这组脚本部署已验收、已发布的四份 release SIF 对应的原生运行文件。
直接读取家目录中已经从 SIF 导出并逐文件校验的 `.native`，不会重新编译。
`release.json` 固定四份 SIF、原生清单和导出记录的 SHA256。

服务器上的准备目录：

```text
/home/stardust/sai-hpc-software/deployment/cp2k-2026.2/
```

## 安装内容

- `bin/cp2k.psmp`、`cp2k.popt`，以及 CP2K 自带的 `dumpdcd`、`graph`、`xyz2dcd` 用户工具。
- `lib64/libcp2k.so*`，以及交付前缀内的私有 ELPA、TBLITE、Spack 运行库和运行数据。
  保留 `libplumedKernel.so`、运行支持文件及所有保留库的符号链接。
- 完整的原有 `share/cp2k/data`，内容、子目录和链接均保持原样。
- `share/sai/native-module.tcl`、构建身份、依赖来源记录，以及如实列出此次精简结果的
  `share/sai/deployment.json`。

按用户要求，不额外挑选或添加 examples/benchmark。只部署 CP2K 自带的原有运行数据。
剔除单元测试程序、miniapp、CMake/pkg-config 开发文件、ELPA 构建日志、
LIBXS 开发文档，以及 PLUMED 的外部软件补丁、Fortran 开发接口和编辑器文件。
保留的链接若引用了被剔除文件，预检会直接失败。

站点已安装的 GCC、MPI、CUDA、HCOLL、FFTW、LibXC、SAIBLAS、spglib 等继续由
原生 module 引用，不向 CP2K 目录复制整套工具链。HCOLL 使用真实 HPCX 路径，
无需创建 `/opt/mellanox/hcoll`。

本次真实预检的运行文件大小（不含很小的新增部署清单）：

| 分区 | 普通文件数 | 大小 |
| --- | ---: | ---: |
| 4V100 | 447 | 483.67 MiB |
| 16V100 | 447 | 480.50 MiB |
| 8V100V0 | 447 | 480.82 MiB |
| DSPRHBM | 446 | 473.17 MiB |

总计约 1.87 GiB。`deployment-plan.json` 是在服务器上生成的逐文件清单，
同时列出拟部署和剔除的文件；无需另外复制原生导出之外的源码、系统镜像或构建缓存。

## 跨账号权限

`--check` 只需要读取安装包、四份 SIF、`.native` 和产物记录，以及进入它们的父目录；
不需要对 stardust 的家目录或产物增加写权限。目录的 `x` 权限用于进入目录，
只给文件 `r` 而不给父目录 `x` 仍会报 `Permission denied`。

已为 `saiuser01` 在阻挡访问的指定目录添加 `r-x` ACL，并为原验收输入添加 `r--` ACL；
原生导出内的文件原本已可读，不改动其内容、权限或冻结清单。
ACL 不向该账号授予写权限，也不开放整个家目录或其他软件的私有目录。
换其他账号执行时，目录所有者需为该账号设置对应的只读 ACL。

`--install` 另外需要目标 `/opt/apps/cp2k` 和 module 目录的写权限，
可以用有管理权限的账号或下面的 `sudo` 命令执行。

## 执行顺序

在 SAI-stardust 登录节点先预检；默认也只做预检：

```bash
cd /home/stardust/sai-hpc-software/deployment/cp2k-2026.2
bash install.sh --check
```

用具有目标 `/opt` 写权限的账号安装，例如：

```bash
sudo bash /home/stardust/sai-hpc-software/deployment/cp2k-2026.2/install.sh --install
```

脚本先核验四份产物、保留文件的 SHA256、已有安装及 module 冲突。
随后在目标分区目录的同一文件系统建立隐藏暂存目录，复制保留文件、回读校验，
再更名为最终分区目录。四个目录全部就绪后，才原子发布公用 module：

```text
/opt/modules/modulefiles/apps/cp2k/2026.2-auto
```

已经存在且完全相同的安装可重复执行；不同内容会报错并保留现状。
失败时尚未发布的 `.分区.incoming-进程号` 暂存目录会保留供检查。
脚本不删除原 SIF、原生导出、旧软件或旧 module，也不修改 `.version` / `.modulerc`。
`--report /家目录/新文件.json` 可另外保存逐文件清单，文件必须尚不存在。

部署路径严格保留编译时前缀：

```text
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r4d454c740309/4V100/
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r4d454c740309/16V100/
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r4d454c740309/8V100V0/
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-rfb4adb0598fc/DSPRHBM/
```

CPU 和 GPU 配方身份不同，因此不改名合并安装目录。

## 安装后检查和使用

普通用户可执行物理文件校验；此步不需要原 SIF 或 `.native`：

```bash
bash /home/stardust/sai-hpc-software/deployment/cp2k-2026.2/install.sh --verify
```

然后以普通集群用户提交四分区原生运行验收：

```bash
bash /home/stardust/sai-hpc-software/deployment/cp2k-2026.2/submit-native-checks.sh all
```

也可把 `all` 换成一个分区名称逐个执行。每分区提交一个双节点任务，使用已发布
SIF 的原始七种输入，分别跑单节点和双节点，共 14 项；结果与该分区已验收 SIF 的
实际能量、力比较。计算在物理 `/opt` 下直接执行，使用安装目录的数据；
没有容器启动器。版本、功能标志、每 rank 的节点及动态库加载一并检查，
HCOLL 必须从真实 HPCX 目录加载。

原产物和科学参考仍从 `/home/stardust/sai-hpc-software` 只读访问。
新结果默认写入执行账号自己的 `$HOME/sai-hpc-software/runtime-tests/cp2k-2026.2-physical-*/<分区>/`，
MPI 临时文件使用同一工作目录下较短的 `runtime/cn-<job>` 路径，避免 Unix socket
文件名长度限制；CUDA 等缓存也写入新结果目录，均不使用节点 `/tmp`。
可通过 `SAI_CP2K_NATIVE_WORK_ROOT` 指定本人家目录下更短的工作路径；
`SAI_SOFTWARE_ROOT` 单独指定原产物和科学参考的位置，不改变结果目录。
任务成功后生成 `native-acceptance.json`，
并在 Slurm 日志中写出 `CP2K_PHYSICAL_NATIVE_ACCEPTANCE_PASSED`。
此验收脚本复用原始冻结控制器中的科学解析器和原始 SIF 验收记录，
所以应保留家目录中的对应 `controller/`、产物侧车和 `runtime-tests/` 科学证据。
这些维护者材料不安装进 `/opt`，普通 CP2K 用户运行也不依赖它们。

用户作业在获得 Slurm allocation 后加载：

```bash
module use /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
mpirun ... cp2k.psmp -i input.inp
```

module 只按当前 `SLURM_JOB_PARTITION` 选择版本和记录过的依赖。
MPI rank、GPU 映射、OpenMP 线程数仍按作业本身设置；卸载使用加载时保存的分区。

当前记录的是部署包预检通过；管理员物理安装和随后的原生科学验收须执行上面的步骤。
