# GPUMDkit 1.5.9

上游正式版本为 [v1.5.9](https://github.com/zhyan0603/GPUMDkit/releases/tag/v1.5.9)，
提交 `073371a45c9f58b0c0e2ca9ba7a7934705c6a677`，2026-10-21 发布，GPL-3.0。
它提供格式转换、训练集筛选/采样、NEP 预测、结构和热输运分析、绘图与作业准备。
集群旧 `/opt/apps/GPUMDkit/main-20260207` 的实际 banner 是 `1.5.1 (dev)`；
新版本包含 LAMMPS 压缩 data 转换、热输运量子修正、主动学习选帧与应力符号修复，
有独立预装价值。

## 产物与依赖

只提供**一份通用 x86_64 安装**，不按 V100 分区复制。Python 3.12.13 和
常用 NumPy/SciPy/ASE/Matplotlib/Pandas/Pymatgen/Scikit-learn/DPData 等库随环境安装。
上游推荐的 Calorine 4.1、NepTrain 2.2.0 原生扩展也包括在内，使用 GCC 14.3.0，
显式 `-march=x86-64 -mtune=generic`，无 `-march=native` 或分区专用 AVX-512 选项。
第三方 manylinux wheels 可以在运行时选择适合 CPU 的优化实现。

这是数据准备和分析工具。实际 GPUMD/NEP 训练或 MD 引擎仍由各自 module 提供；
不依赖或修改 LAMMPS/NEPAdapters。OVITO、DeepMD/DPA 预测、旧 PyNEP、ferrodispcalc、
NepTrainKit GUI 属于另需对应包的可选功能，本安装的静态帮助明确标注范围。
Calorine/NepTrain 在这里运行 CPU 预测，无需宿主 MPI 或 GPU。

## 管理员安装

已验收的 SIF 及证据由 `prepare.py` 组成可读交付包。
SIF **267.0 MiB**；解压后标准文件总大小 **872.5 MiB**（22,393 个普通文件）：

```bash
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --check
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --install
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --verify
```

安装脚本从已验收 SIF 导出精确路径，核对文件哈希、权限和软链接，最后发布 module。
原生权限统一为目录和可执行文件 0755、普通文件 0644；SIF 原始清单中的
构建账户组写权限不作为公共安装要求。先完整核对内容、类型和软链接，再规范
暂存目录中的权限并严格复核，避免解包工具或账户差异造成安装误报。
不会运行上游修改 `.bashrc` 的 `install.sh`，不会覆盖旧版本。
重复安装相同内容可行；已存在不同内容时停止。

若上一次已经完整解包，只因权限检查失败，可以显式复用报错时留下的暂存目录：

```bash
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --install \
  --resume-stage /opt/apps/GPUMDkit/.1.5.9-incoming-1609074
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --verify
```

恢复只接受安装账号拥有的同级 `.1.5.9-incoming-<pid>` 目录，并重新校验全部
文件内容。成功后将已有文件直接移至最终路径，再移除空的解包目录；不会重复
提取一份产物。首次安装仍使用普通 `--install`。

```text
/opt/apps/GPUMDkit/1.5.9/
  bin/gpumdkit
  bin/gpumdkit.sh
  bin/python[3.12]
  lib/python3.12/site-packages/
  lib/                         Python 与数值库运行时
  include/                     标准 Python/依赖头文件
  share/gpumdkit/               上游 Scripts、src、必要模板/帮助
  share/licenses/gpumdkit/LICENCE
  share/bash-completion/completions/gpumdkit
/opt/modules/modulefiles/apps/gpumdkit/1.5.9
```

构建缓存、Conda 交易记录、验收脚本和日志不放入应用目录。
Python 包的标准 `.dist-info` 保留，以支持包版本查询和资源定位。

## 用户入口

```bash
module load gpumdkit/1.5.9
gpumdkit -h
gpumdkit -doctor
gpumdkit -pos2exyz POSCAR structure.xyz
gpumdkit -prediction train.xyz nep.txt 4
gpumdkit -plt prediction save
```

`module help gpumdkit/1.5.9` 在登录节点直接给出真实安装路径和静态完整帮助：

```bash
cat /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/build-info/index.txt
```

module 只添加应用入口及应用变量，Python 本身使用相对 RPATH 定位自带运行库，
不会向全局 `LD_LIBRARY_PATH` 插入整个 Python lib 目录。
与已安装的 phonopy、SSCHA、exa-amd 对齐：属于 `python` family，通过 PATH
选择 Python，不执行 Conda 激活；加载期间隔离 `PYTHONPATH`/`PYTHONHOME`、禁用
用户 site-packages 和字节码写入，卸载时恢复原值。切换 Python 环境前先卸载，
`SAI_PYTHON_ENV` 指向当前环境。`whatis` 保持一条，包含版本、Python 版本和 Features。
上游 `v1.5.9` 的 CLI banner 仍写 `1.5.8 (2026-10-20)`；保留原始源码，
以 module 和静态记录中的 tag/SHA 标识真实版本。
上游 Conda wrapper 的 `-update` 仅提示 Conda 更新，不会自动修改公共安装；
公共版本更新由管理员发布新版本 module。

## 验收与复现

容器内从固定 Conda seed 和锁定 Python 包安装，编译两个 C++ 扩展后打包为 SIF。
`accept.py` 通过真实 CLI 检查 POSCAR 转换、压缩 LAMMPS data 转换、周期最短距离，
使用固定 Pb/Te NEP4 模型检查能量、力和 virial，执行两进程预测及无显示 PNG 绘图。
新作业同时回测 SIF 和具有相同文件清单的项目暂存原生安装。
暂存原生回测并不代表管理员最终 `/opt` 路径已完成验收。

验收作业 `2375499`（16V100，2m27s）已通过 SIF 和原生暂存路线。
管理员安装脚本另在项目目录以 `umask 077` 实际试安装，再经作业 `2375591`
完成真实 Lmod 加载、全部功能回测和卸载，16 秒通过。
详情见 [验收记录](../../docs/evidence/gpumdkit-1.5.9-20261111/README.md)。

SIF和大文件清单位于集群交付包；Git 只保存脚本、依赖锁定、摘要和静态构建信息。
