# 将 update1 设为默认 LAMMPS

本包准备标准 Lmod 默认版本配置：

```text
module load lammps
  -> lammps/30Sep2026_update1-deepmd320-auto

module load nepadapters
  -> nepadapters/1.0.5-lammps30Sep2026_update1
  -> 自动加载上述 LAMMPS
```

使用同一份已安装 NEPAdapters 1.0.5 插件：
`/opt/apps/nepadapters/1.0.5-lammps30Sep2026/lib`。
不复制插件或 LAMMPS，不改算例，不改用户 `.bashrc`；原有版本号明确的 module 保留。

## 发布条件

必须完成三个分区 `4V100`、`16V100`、`8V100V0` 的正式 `/opt` 原生验收：

- LAMMPS 各 14 个科学算例、功能清单、宿主 MPI 和动态库检查。
- 同一份 NEPAdapters 插件的单 GPU、同节点双 GPU、双节点检查；CPU/GPU 能量、
  力、virial、100 步 GPU MD、周期/混合/非周期边界和 module 卸载检查。
- 重新解析原始输出，并确认各任务的 Slurm 成功状态。

`manifest.json` 仅在所有条件满足后生成。缺少它时 `install.sh` 会停止，
不会修改默认版本。三个分区的完整矩阵现已通过：最后的 8V100V0 正式 LAMMPS
作业 2374775 和 NEP 跨节点作业 2374898 均成功。11 月 11 日 23:21 已生成
完整 manifest；11 月 12 日重新解析原始结果及运行安装前校验也通过。
当前只待管理员安装默认配置。

## 管理员操作

完整交付包准备好后，以有 module 目录写权限的 `saiuser01` 执行：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-update1-default/install.sh --check
bash /home/stardust/sai-hpc-software/deployment/lammps-update1-default/install.sh --install
bash /home/stardust/sai-hpc-software/deployment/lammps-update1-default/install.sh --verify
```

管理员无需读取 `/org`。安装器检查家目录中的验收凭据、当前 `/opt` 程序/插件
及其依赖哈希后，按顺序发布兼容 module 和 help 文案，最后写默认配置。
遇到已有不同的默认配置会停止；重复安装相同内容可安全重试。

只修改这些文件：

```text
/opt/modules/modulefiles/apps/lammps/30Sep2026_update1-deepmd320-auto  （仅 help 文案）
/opt/modules/modulefiles/apps/lammps/.modulerc.lua
/opt/modules/modulefiles/apps/nepadapters/1.0.5-lammps30Sep2026_update1
/opt/modules/modulefiles/apps/nepadapters/.modulerc.lua
```

LAMMPS help 的变化是把旧的“update1 兼容性未验收”替换为明确的兼容插件 module
指引；module 的程序路径、依赖和运行环境部分保持相同。
旧文案文件备份放在执行账号的 `~/sai-hpc-module-backups`。

在登录节点查看，无需加载计算程序：

```bash
module --ignore_cache whatis lammps
module --ignore_cache help lammps
module --ignore_cache help nepadapters
cat /home/stardust/sai-hpc-software/deployment/lammps-update1-default/build-info/index.txt
```

`--ignore_cache` 确保看到刚更新的默认选择。用户加载计算 module 仍须处于 Slurm
作业中。NEP3、CUDA LAMMPS qNEP 和每日 beta 的组合不在本次验收范围内。

## 维护者准备 / 等待既有任务

由 stardust 在项目内运行（命令不再提交计算，也不安装 `/opt`）：

```bash
python3 -B /home/stardust/sai-hpc-software/deployment/lammps-update1-default/prepare-release.py \
  --native-work /org/org-open-sai/proj-sai-hpc-software/operations/lammps-update1-native-20261111-201352-476274 \
  --nep-work /org/org-open-sai/proj-sai-hpc-software/operations/nepadapters-update1-20261111-202034 \
  --controller /org/org-open-sai/proj-sai-hpc-software/operations/lammps-update1-default-20261111/control \
  --wait
```

`--wait` 每分钟检查一次；失败则退出并打印原因。去掉此参数可只检查一次。
全部结束后会调用两个原始验收程序重验，拷贝公开凭据、生成静态 build-info，
并更新家目录原 update1 安装包中的精确 help 迁移记录，使原 `--install`/`--verify`
继续与新文案一致。只有完成全部检查，才生成 `manifest.json` 并打印
`DEFAULT_PACKAGE_READY`；此时才可交由管理员安装。

准备文件、排队任务与实际默认生效是三个不同状态；最终生效须管理员执行 `--install`。

真实 Lmod 预览已确认无版本号的 `show/whatis lammps` 和 `show/whatis nepadapters`
分别解析到 update1 与对应兼容插件 module。预览树包含目标版本文件及两个
`.modulerc.lua`，没有依赖跨 MODULEPATH 查找默认目标的特殊行为。

11 月 12 日作业 2381151 还通过了普通默认加载的实际 GPU 计算：在安装器生成的
私有预览树中执行 `module load lammps`、卸载、`module load nepadapters`，均选择
上述版本；使用物理 `/opt` 的 LAMMPS 和原插件完成 Pb/Te NEP4 数值检查及卸载。
未使用 `--ignore_cache`，沿用站点的 `LMOD_CACHED_LOADS=no`，全局缓存的哈希和
时间戳未改。此测试验证的是待安装配置，公共默认仍需管理员执行安装后才生效。
