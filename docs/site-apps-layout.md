# SAI /opt/apps 同路径构建与只复制部署

2026-09-20 经 SAI-stardust 只读现场检查，新的容器安装前缀使用 identity schema 2。
本次只改变构建/交付布局，不放宽功能、科学结果或性能验收，不修复其他编译问题。
代码更新后须重新构建；旧 overlay / SIF 的路径、校验和、身份与验收证据不能改标。

## 实际站点结构

以下是已存在的目录，不是拟建的另一套 /opt/software：

| 家族 | 现场已有版本示例 | 新构建版本目录 |
| --- | --- | --- |
| /opt/apps/abacus | abacus-v3.11.0-beta6-sm70-nvhpc263-avx2 | abacus-<track>-<build_id> |
| /opt/apps/cp2k | cp2k-2026.1、cp2k-2026.1-avx2 | cp2k-<track>-<build_id> |
| /opt/apps/deepmd-kit | deepmd-kit-3.2.0、dev、master | deepmd-kit-<track>-<build_id> |
| /opt/apps/lammps | lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70 | lammps-<track>-<build_id> |
| /opt/apps/gpumd | GPUMD-5.8、GPUMD-master | GPUMD-<track>-<build_id> |

版本目录下每个 Slurm 分区独立构建。例如：

```text
/opt/apps/abacus/
  abacus-v3.11.0-beta6-sm70-nvhpc263-avx2/     # 保持不动
  abacus-release-<build_id>/
    DSPRHBM/
    4V100/
    16V100/
    8V100V0/
      bin/
      dependencies/                         # 本次构建的私有依赖（如有）
      share/sai/                            # 身份、环境、清单、module fragment
      modulefiles/abacus/release/<build_id>  # 通用分区 selector
```

build_id 包含上游版本、源码摘要、配方摘要；DeepMD/LAMMPS 还包含配对摘要。
CMake install prefix、Python venv、私有依赖/Spack store 从创建时即使用最终绝对路径。
源码、编译树和打包工作区仍在容器 /workspace 中，不能当作运行依赖交付。
CP2K 的 Spack 配置现在接受新前缀；独立 Spack pilot 尚不等于已接入 CP2K 主配方。
旧 pilot 的固定路径用于诊断检查点，不能作为新布局产物发布。

## 依赖 module 与挂载

在计算节点作业体内初始化 Lmod 并加载现有基础设施 module，不在登录节点预先选 ISA：

- openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
- saiblas/2603-gnu-auto
- libxc/7.0.0-auto
- elpa/2026.02.001-2603-gnu（其内部再加载上述 MPI/BLAS auto module）
- 按配方加载 CUDA、NVIDIA MP 库、FFTW 等固定版本。

现场 auto module 检测 avx512_vnni，否则选择 avx2。因此 DSPRHBM、4V100 用
AVX512 依赖，16V100、8V100V0 用 AVX2 依赖；程序本身仍分别原生编译，
不能把两个 AVX2 依赖分区的程序合并。新软件 selector 按 SLURM_JOB_PARTITION
选择对应程序，再加载已经记录的依赖 module。

基础 toolchain 确实会有绝对 RPATH、pkg-config/CMake 路径，不移动或重写它们。
/opt/devtools、系统库及具体版本的 /opt/apps 依赖以原路径只读复用。
remote_controller.SITE_APPS 记录已检查的依赖目录；CP2K avx512 是指向
cp2k-2026.1 的符号链接，挂载保留两种原始访问路径。
严禁把宿主整个 /opt/apps 或 /opt/apps/<软件家族> 挂进容器遮住候选安装树。
不能把宿主既有应用目录一起 cp 到新 SIF。

## 交付与管理员命令

由 stardust 使用 export_native.py 从 SIF 做清单/散列校验导出。选择单个独立软件
分区时，导出目录本身就是可复制的完整安装树：

```bash
python3 controller/export_native.py export /绝对路径/产物.sif \
  --image-sha256 "<已核验的SIF完整SHA256>" \
  --prefix "/opt/apps/abacus/abacus-release-<build_id>/16V100" \
  --destination /home/stardust/交付目录/16V100
```

这里的尖括号是必须用新构建身份替换的占位符，不表示已有新产物。
导出后的 share/sai/export.json 和 README-export.txt 写明准确目标路径。
DeepMD/LAMMPS 自动连同锁定伙伴导出；必须复制两份对应安装树。

管理员只需拥有目标目录写入/访问权限，不要求 root、sudo，也不运行编译器。
以下示例每次复制一个分区；来源目录应为 stardust 已校验的导出目录：

```bash
src=/home/stardust/交付目录/16V100
build_id='替换为此次交付的实际build_id'
version="/opt/apps/abacus/abacus-release-$build_id"
dst="$version/16V100"

test -d "$src" && test -r "$src/share/sai/export.json"
test -w /opt/apps/abacus && test -x /opt/apps/abacus
test ! -L "$version"
test ! -e "$dst" && test ! -L "$dst"
mkdir -p -- "$version"
cp -a --no-preserve=ownership --no-clobber -T -- "$src" "$dst"
```

这些命令应逐条检查成功，或放在 set -e 的脚本内；不覆盖既有安装。
目标前缀及其父目录必须为真实目录而非符号链接。
若复制中途失败，保留未完成目录并调查，不允许在其上静默合并后宣称校验成功。
管理员不需要改二进制、脚本、RPATH、shebang 或 CMake/pkg-config 元数据。

stardust/验收负责人随后在对应计算分区做原生科学与同资源性能验收。
无需管理员承担该验收。通过后，管理员可只复制版本 selector 到现场 module 目录：

```bash
module_parent=/opt/modules/modulefiles/apps/abacus/release
test -w /opt/modules/modulefiles/apps/abacus
mkdir -p -- "$module_parent"
test ! -e "$module_parent/$build_id" && test ! -L "$module_parent/$build_id"
cp -a --no-preserve=ownership --no-clobber -- \
  "$dst/modulefiles/abacus/release/$build_id" "$module_parent/$build_id"
```

所有分区共享同一个 module 名 abacus/release/<build_id>，会自动选择分区；
没有安装的分区明确报错，不回退到别的分区。验收阶段也可直接 module use
完整安装目录下的 modulefiles，无需先发布系统 module。
不要把该原生 selector 与调用 Apptainer 的容器 launcher module 混用。

## 生效与兼容边界

本地代码/测试更新本身不修改正在运行的控制器快照，也不会提交新 Slurm 作业。
部署新的控制器版本后，只有新构建才使用新布局；recipe 摘要也会改变。
既有发布和运行中任务继续由原冻结快照负责，不能用新控制器强行续编旧 overlay。
schema 1 的 /opt/software 身份仍可校验与导出，但不会变成 schema 2。
原生科学/性能验收必须针对实际复制后的安装树；本地单元测试和散列正确不能替代它。
