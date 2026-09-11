# 分区化构建与原生交付

本规范区分三件事：源码身份、通过验收的容器产物、从容器提取的原生安装树。
清单与校验和只证明文件完整性，不能替代科学结果、功能和同资源速度验收。
旧布局 SIF 不会自动获得新身份，也不能通过改目录名冒充新布局构建。

## 渠道、分区和命名

五种软件统一使用 `development`、`prerelease`、`release` 三个渠道。
每日检查各渠道最新上游 SHA；GitHub Actions 的定时器仅在默认分支运行，
实验分支需要手动触发。未发布 prerelease 的上游可明确记为 skipped；网络、
API、标签解析失败必须报错，不能伪装成“没有版本”。release 使用标签指向的
commit，annotated tag 必须解引用，不能误取同名分支。

| 软件 | 开发分支 | 原生构建分区 |
| --- | --- | --- |
| ABACUS | develop | DSPRHBM、4V100、16V100、8V100V0 |
| CP2K | master | DSPRHBM、4V100、16V100、8V100V0 |
| DeePMD-kit | master | 4V100、16V100、8V100V0 |
| LAMMPS | develop | 4V100、16V100、8V100V0 |
| GPUMD | master | 4V100、16V100、8V100V0 |

安装前缀从首次配置、编译时即固定为：

```text
/opt/software/<software>/<track>/<build_id>/<PARTITION>
build_id = <version_label>-g<source_sha12>-r<recipe_sha12>[-s<stack_digest12>]
```

例如 `.../abacus/release/v3.11.0-g0123456789ab-rabcdef012345/4V100`。
这里的短 SHA 仅为示意，不代表已通过验收的 3.11.0 安装。
完整 source SHA、recipe SHA、原始 ref/version、分区、CPU、CUDA 与依赖 ISA
均保存在 `release_contract.make_identity()` 生成的身份对象中。
DeepMD/LAMMPS 成对交付额外锁定两份源码；同分区的 companion 必须匹配
完整 stack digest，不能把不同配对的组件拼在一起。
每个组件有新源码都可触发配对构建：优先使用伙伴的同渠道版本，仅当伙伴明确
没有该渠道时使用其最新稳定版。两个身份各自保留真实渠道，不把稳定版重新
标成预发布；若连稳定伙伴也不存在，则明确报告原因。相同的双身份组合去重。

`8V100V0` 的 CPU 是 Skylake AVX512，但站点依赖必须使用 AVX2 版本；
不能因分区具备 AVX512 就加载依赖 VNNI 的 Zen4 库。所有目标仍在对应分区
本机编译，路径标签不能替代实际架构检查。

## 制作可导出的镜像

构建器在容器中安装完成后写纯数据 entry，包含：

```json
{
  "identity": "此处必须是完整 make_identity 对象，不是字符串",
  "commands": {"abacus": "bin/abacus"},
  "external_roots": ["/opt/modules/modulefiles/devtools"],
  "runtime": {
    "modules": [],
    "prepend": {"MODULEPATH": ["/opt/modules/modulefiles/devtools"]},
    "set": {"OMP_NUM_THREADS": "1"}
  }
}
```

以上只是字段示意，不是可直接使用的 ABACUS 环境配方。
实际 entry 必须记录现场验证过的依赖模块和库路径；优先具体 ISA 的模块版本。
例如 `nvmplibs/26.7-tmp`、预装 ELPA、PLUMED 可以作为普通 `module load`
依赖，不需要随包复制预装工具链。若站点只有 `*-auto` 模块，还需验证其
load/unload 的分区选择行为，不得为加载模块而改写 `SLURM_*` 或 GPU 分配变量。

在文件系统 overlay 中运行：

```bash
python3 /control/export_native.py inventory --entry /workspace/native-entry.json
# 成对安装可重复 --entry；输出 /opt/sai-delivery/manifest.json。
```

该命令只扫描声明的安装前缀，不执行 runtime shell。
新增依赖若不在系统中，应安装到交付前缀内的明确子目录，或按同一契约另行
打包；不能偷偷依赖 `/workspace`、`/control`、宿主用户目录或旧的共用临时前缀。
普通文件必须全员可读，可执行文件全员可执行，目录全员可读、可遍历；
拒绝组/其他用户可写、setuid、设备文件、FIFO、越界链接和不完整配对。
建议容器安装阶段 `umask 022`，宿主任务目录仍可保持私有权限。

## 提取到任意暂存位置

工具依赖 Python 3、Apptainer 的 SIF 元数据命令和 Squashfs-tools 4.6.1。
不启动镜像中的程序，不执行镜像环境脚本，不解出整个根文件系统，不需要 root。

```bash
python3 controller/export_native.py export /shared/artifacts/build.sif \
  --image-sha256 <验收产物记录的完整SHA256> \
  --destination /shared/staging/abacus-release-build
```

目标目录必须不存在，父目录必须已存在且不经过符号链接。不会覆盖现有文件，
也不会自动往系统 `/opt` 安装。失败时只清理脚本自己创建的私有暂存目录；
若交付目录已经开始移动，则保留部分结果并明确报错。

```text
abacus-release-build/
  rootfs/opt/software/abacus/release/<build_id>/4V100/...
  modulefiles/abacus/release/<build_id>
  inventory.json
  export.json
  README.txt
```

`export.json` 列出每个真实 `/opt` 安装前缀、镜像/清单校验和及生成 module
的校验和。只提取清单中的安装树；实际 SquashFS 文件类型、权限、大小与清单
逐项对照，普通文件内容再按 SHA256 校验。链接必须逐路径组件解析，不能靠
字符串规范化绕出边界；每个链接还会按字面路径单独核验。

## 物理部署与 module 使用

把 `rootfs` 下所需分区的树复制到记录的绝对位置，或者让共享存储挂载在该
规范路径；保留内部符号链接和 POSIX 权限。此脚本不做自动特权安装。
`modulefiles` 可放在任意共享位置：

```bash
module use /shared/software/modulefiles
# 在实际 Slurm allocation 中：
module load abacus/release/<build_id>
```

selector 按 `SLURM_JOB_PARTITION` 找对应 `/opt/.../<PARTITION>/share/sai/native-module.tcl`，
要求数字 `SLURM_JOB_ID`，不接受在登录节点随意假选一个 ISA。
`module help/show` 不需要分配，也不执行 payload 或加载依赖。
卸载使用已加载时保存的分区，不重新按当前分区选库。模块直接把原生 `bin`
加入 PATH，不调用 Apptainer 包装器；站点依赖优先用 `depends-on` 保留引用计数。
未安装分区、错误身份或符号链接父路径会明确报错。

“导出到任意位置”只指暂存位置，不承诺任意运行位置。编译时的 RPATH、Python
解释器路径、CMake/pkg-config 数据和数据文件位置可能包含绝对前缀；不要将
module 中 `/opt` 替换为暂存路径后就宣称可重定位。正式物理部署必须另测
`ldd`、版本/功能、科学算例和同资源速度，并保留镜像/身份对应的原始证据。

## 当前验证范围

三渠道与身份契约、原生 module 生成器、导出工具均有独立测试；导出专项包含
真实 SquashFS、四分区路径、多分区共用 selector、权限、链接穿越、伪造文件
边界、配对源码不一致、已有目录保护。工具通过不代表五种软件均已按本规范
重建/导出/完成科学与速度验收。尤其旧 CP2K 依赖布局及 MD 三后端模型验证
仍需对应软件分支完成，不能复用旧 SIF 的验收标签。

已知启动性能待办：目前容器 launcher 每个 MPI rank 都会完整 SHA256 校验
镜像。N ranks 对 S 字节镜像造成 N×S 的逻辑读取和哈希工作，实际存储流量
取决于缓存。这可能影响含启动时间的 benchmark；正式速度验收前需实现可信
job/node 级校验复用，或者测试实际原生部署，不能删掉校验后直接宣称提速。

2026-09-11 在 `SAI-stardust` 现场用 Apptainer 1.4.4 / Squashfs-tools 4.6.1
验证了四分区小型 SIF 的完整导出，结果 PASS。探针位于
`/home/stardust/sai-hpc-software/experimental/native-export-20260911-r1`，
样品明确标识 `NOT-SOFTWARE-EXPORT-PROBE`，没有执行容器、没有修改系统 `/opt`，
不属于软件科学产物。SIF SHA256：
`d2f85f51a9533d2823aa6f12097449eb16a457da3d2f77d42e6372bbf72af9ed`。
四份原生 fragment 和一份共用 selector 的校验和保存在现场 `export/export.json`。
另已现场用真实 Lmod 验证：登录节点 `module show` 正常，`module load` 因没有
Slurm allocation 按预期退出 1，未设置 `SAI_ABACUS_PREFIX`，也未加载 payload。
`controller/probe_native_export.py` 提供可重复生成与验证流程；本地 fixture 需要
真正支持 POSIX 权限的文件系统，不能用将权限统一映射为 0777 的挂载盘冒充。
