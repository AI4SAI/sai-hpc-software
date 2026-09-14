# 分区化构建与原生交付

本规范区分三件事：源码身份、通过验收的容器产物、从容器提取的原生安装树。
清单与校验和只证明文件完整性，不能替代科学结果、功能和同资源速度验收。
旧布局 SIF 不会自动获得新身份，也不能通过改目录名冒充新布局构建。

交付单位是一份**完整安装文件夹**。程序、数据、安装清单、依赖说明和 module
都在这个文件夹内部，不要求另建 `/opt/sai-delivery`，也不要求复制外置的
modulefiles 或校验清单。系统预装依赖仍只读复用，不随安装树重复复制。

## 渠道、分区和命名

五种软件统一使用 `development`、`prerelease`、`release` 三个渠道。
目标是每日检查各渠道最新上游 SHA；目前默认分支仅开启已接科学验收的 ABACUS
自动构建/发布。CP2K 暂限手动候选构建，直接 publish 也拒绝无科学验收的候选；
其余软件仍在独立实验分支。GitHub Actions 的定时器仅在默认分支运行。
未发布 prerelease 的上游可明确记为 skipped；网络、
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
开发版使用上游提交的 **UTC 日期**，日期在 SHA 前，例如
`.../abacus/development/develop-2026-09-13-g0123456789ab-rabcdef012345/4V100`。
日期从已锁定 SHA 的 commit 元数据读取，不用当天日期替代；同一源码跨天重跑
不会改变安装身份或制造每日缓存失效。稳定版和预发布版保留标签名称，SHA 由
共享身份逻辑统一追加一次，不在版本标签中重复。
实际 SIF 文件名采用 `<run>-<attempt>-<track>-<target>-<提交任务UTC日期>-<source_sha12>.sif`；
此处日期描述构建任务，安装目录日期描述源码提交，Actions run/attempt 区分重试。
完整 source SHA、recipe SHA、原始 ref/version、分区、CPU、CUDA 与依赖 ISA
均保存在 `release_contract.make_identity()` 生成的身份对象中。
DeepMD/LAMMPS 成对交付额外锁定两份源码；同分区的 companion 必须匹配
完整 stack digest 与同次构建配方 SHA，不能把不同配对或不同配方的组件拼在一起。
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
# 成对安装可重复 --entry；在各自安装目录内生成 share/sai/manifest.json。
```

该命令只在声明的安装前缀中生成原生 module 和清单，不执行 runtime shell。
模块先生成，再计入清单的文件哈希；清单排除自身，避免“文件记录自己哈希”
的循环，其完整性由 SIF 校验和及导出记录绑定。
新增依赖若不在系统中，应安装到交付前缀内的明确子目录，或按同一契约另行
打包；不能偷偷依赖 `/workspace`、`/control`、宿主用户目录或旧的共用临时前缀。
普通文件必须全员可读，可执行文件全员可执行，目录全员可读、可遍历；
拒绝组/其他用户可写、setuid、设备文件、FIFO、越界链接和不完整配对。
命令入口允许软链接，例如 `bin/abacus -> abacus_max_gpu`；清单和导出保留链接
原文，最终目标必须是兼容交付前缀内随包提供的可执行普通文件，不能只指向预装程序。
非配对软件若链接到另一安装前缀，导出时须用多个 `--prefix` 同时选择这些前缀。
建议容器安装阶段 `umask 022`，宿主任务目录仍可保持私有权限。

## 提取到任意暂存位置

工具依赖 Python 3、Apptainer 的 SIF 元数据命令和 Squashfs-tools 4.6.1。
不启动镜像中的程序，不执行镜像环境脚本，不解出整个根文件系统，不需要 root。

```bash
python3 controller/export_native.py export /shared/artifacts/build.sif \
  --image-sha256 <验收产物记录的完整SHA256> \
  --prefix /opt/software/abacus/release/<build_id>/4V100 \
  --destination /shared/staging/abacus-release-build
```

目标目录必须不存在，父目录必须已存在且不经过符号链接。不会覆盖现有文件，
也不会自动往系统 `/opt` 安装。失败时只清理脚本自己创建的私有暂存目录；
若交付目录已经开始移动，则保留部分结果并明确报错。

```text
abacus-release-build/
  bin/
  lib/                         # 视实际安装内容而定
  share/                       # 软件数据也在安装树内
    sai/
      manifest.json
      native-module.tcl
      export.json
      README-export.txt
  modulefiles/abacus/release/<build_id>
```

指定一个独立软件安装时，`--destination` 本身就是完整安装文件夹，没有
`rootfs` 外壳。批量导出时，在目标下按 `<software>/<track>/<build_id>/<PARTITION>`
排列多个完整文件夹，每份都有自己的清单和 module。若选择 DeePMD/LAMMPS
配对的一方，会一并选择清单锁定的伙伴，不能只取一方后宣称配套依赖齐全。

`share/sai/export.json` 列出该文件夹对应的真实 `/opt` 安装前缀、镜像/清单
校验和及配套安装需求。只提取清单中的安装树；实际 SquashFS 文件类型、权限、大小与清单
逐项对照，普通文件内容再按 SHA256 校验。链接必须逐路径组件解析，不能靠
字符串规范化绕出边界；每个链接还会按字面路径单独核验。镜像中附带的 module
还必须与受校验环境说明生成的内容完全一致，不接受任意 Tcl 脚本冒充。

## 物理部署与 module 使用

把整个 `abacus-release-build` 文件夹复制到清单记录的安装路径，保留内部符号
链接和 POSIX 权限；不必单独寻找或复制清单与 module。此脚本不做自动特权安装。
部署完成后，直接使用安装目录自带的 `modulefiles`：

```bash
module use /opt/software/abacus/release/<build_id>/4V100/modulefiles
# 在实际 Slurm allocation 中：
module load abacus/release/<build_id>
```

selector 按 `SLURM_JOB_PARTITION` 找对应 `/opt/.../<PARTITION>/share/sai/native-module.tcl`，
要求数字 `SLURM_JOB_ID`，不接受在登录节点随意假选一个 ISA。
`module help/show` 不需要分配，也不执行 payload 或加载依赖。
卸载使用已加载时保存的分区，不重新按当前分区选库。模块直接把原生 `bin`
加入 PATH，不调用 Apptainer 包装器；站点依赖优先用 `depends-on` 保留引用计数。
未安装分区、错误身份或符号链接父路径会明确报错。
每份安装附带相同版本的分区 selector，仍按实际 allocation 选择已部署的
分区前缀，不会因为 MODULEPATH 来自某个分区就强制运行该分区二进制。

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

容器 launcher 的完整镜像校验现在按 Slurm job ID 和实际 hostname 复用。
首个 rank 持锁校验完整 SHA256，后续 rank 仍检查身份、侧车和文件状态；
可观察的 inode、大小、mtime、ctime 或侧车变化会使缓存失效。该优化依赖
镜像只读不变的约定，不等价于每 rank 重新检出元数据未变的存储损坏。无 Slurm job ID
时仍全量校验，构建、发布、科学证据复核也始终保留完整校验。锁与小型校验记录
位于项目已有 `runtime/jobs/<job>/` 下，不在安装包里；单个 rank 退出时不删除
共享记录。此机制减少每 rank 重复读取整份镜像，不是针对同 UID 写入者的安全隔离。
同节点共享文件系统锁已按下述专项现场验证；多节点启动耗时及科学程序速度
仍未测量，不能从并发测试推断吞吐提升，旧镜像验收不能重贴到新 launcher 上。

现场 Lustre 的 mtime/ctime 有效精度为秒；毫秒级测试变更可能被截断。同一时间
粒度内、其余观测字段也不变的内容变化不在 stat 缓存的检测保证内。

2026-09-13 合并前增量：命名与 CP2K 候选门禁全仓 161 项通过；加入 runtime
校验复用后主 agent 独立跑全仓 178 项通过。`native_delivery_module` 实施门禁，
`self_contained_export` 独立复核相关专项；`runtime_hash_fix` 实施校验复用，
`runtime_hash_review` 独立复跑 17 项专项和 33 项既有交付/runtime/module 回归。
主 agent 另在 SAI 的 Lustre 隔离目录，用冻结 `532d186` 控制器/测试快照运行
17 项专项，全部通过（1.788 秒），包括 8 进程并发仅完整哈希一次。
快照文件 SHA256 已与本地核对；目录及日志为
`/home/stardust/sai-hpc-software/experimental/runtime-cache-probe-20260913-r2.e09V7k/tests.log`。
首次 `5541ed4` 探针在 `experimental/runtime-cache-probe-20260913.Iwo4nD/tests.log`
保留：15 项通过、2 项因毫秒级变更被秒级时间戳抹平而失败。后续仅修正测试
以制造并断言实际时间戳变化，生产逻辑未改，未覆盖或重标首次记录。
所有合成数据使用隔离目录内 TMPDIR，未使用宿主 `/tmp`，未提交 Slurm、
未运行科学软件、未修改物理 `/opt`。这不是实际软件速度验收。

2026-09-13 新布局验证：共享测试 155 项通过，其中导出专项 22 项、原生 module
25 项包含真实 SquashFS/Tcl 测试。两位子 agent 分别实现导出与 module 打包，
module agent 还独立审查和复跑导出测试；主 agent 独立复跑并在 SAI 现场验证
小型 SIF 的“四分区批量导出”和“只选 4V100 导出一个完整文件夹”，均 PASS。
新探针位于 `/home/stardust/sai-hpc-software/experimental/native-folder-export-20260913-r1`，
对应代码 `ac5f20d`，镜像 SHA256：
`aaed6b90366b2232517be0d78c9a6839461369dff21a89f2b8009fe8d4f81f88`。
`export-4V100` 直接包含 `bin`、`modulefiles` 和 `share/sai/manifest.json`，
没有 `rootfs` 外壳；清单和 module 从镜像原样提取并校验。
探针明确为 `NOT-SOFTWARE-EXPORT-PROBE`，未执行软件、未修改系统 `/opt`，
现场还确认宿主机 `/opt/sai-delivery` 不存在。该验证不能替代正式软件的重编、
科学与速度验收。

历史工具探针：2026-09-11 在 `SAI-stardust` 用 Apptainer 1.4.4 / Squashfs-tools 4.6.1
验证过旧版四分区小型 SIF 导出，结果 PASS。它使用旧版外置清单布局，**不能
作为本次单文件夹布局的验收证据**。历史探针位于
`/home/stardust/sai-hpc-software/experimental/native-export-20260911-r1`，
样品明确标识 `NOT-SOFTWARE-EXPORT-PROBE`，没有执行容器、没有修改系统 `/opt`，
不属于软件科学产物。SIF SHA256：
`d2f85f51a9533d2823aa6f12097449eb16a457da3d2f77d42e6372bbf72af9ed`。
旧探针校验和保存在现场 `export/export.json`；该实验目录保留，不重标为新格式。
另已现场用真实 Lmod 验证：登录节点 `module show` 正常，`module load` 因没有
Slurm allocation 按预期退出 1，未设置 `SAI_ABACUS_PREFIX`，也未加载 payload。
`controller/probe_native_export.py` 提供可重复生成与验证流程；本地 fixture 需要
真正支持 POSIX 权限的文件系统，不能用将权限统一映射为 0777 的挂载盘冒充。
