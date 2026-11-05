# QE 7.6 native release

管理员安装包位于：

```sh
cd /home/stardust/sai-hpc-software/deployment/qe-7.6
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

默认行为与 `--check` 相同，只做检查。`--install` 先核对四份已验收 SIF、科学验收记录、
导出清单与 SHA256，再从 SIF 读取选定文件到安装目录，最后发布模块。
它使用 `apptainer sif list` 和 `unsquashfs -cat` 读取文件，不执行镜像中的程序。
同内容重复安装可继续；已有不同文件、重定向路径或不同模块会报错并保留原内容。
`--verify` 只核对已安装的文件及模块，不再依赖原始镜像。

`release.json` 和 `evidence/` 固定了四份已验收产物的完整来源、文件清单及摘要。
`images/` 保存原始压缩 SIF 的只读副本，共 **3.26 GiB、4 个文件**；该目录不入 Git。
这样管理员不需要访问受限的 `/org` 项目路径，家目录也不用展开软件树。

## 安装目录

```text
/opt/apps/qe/
├── qe-release-qe-7.6-g9f93ddec427d-r02bdbc94280c/
│   ├── 16V100/
│   ├── 4V100/
│   └── 8V100V0/
└── qe-release-qe-7.6-g9f93ddec427d-r18629d40806e/
    └── DSPRHBM/

每个分区目录：
├── bin/                         # 67 个原生 ELF 程序
│   ├── pw.x
│   ├── ph.x
│   ├── pp.x
│   ├── neb.x
│   ├── pwcond.x
│   └── ...
└── share/licenses/
    ├── qe/License
    ├── fox/LICENSE
    ├── mbd/LICENSE
    ├── wannier90/LICENSE
    └── hdf5/COPYING

/opt/modules/modulefiles/apps/qe/7.6-auto
```

每个分区 **72 个文件**，合计 **6.96 GiB**。三个 GPU 分区分别约
1,976 / 1,962 / 1,995 MiB，CPU 约 1,191 MiB。CPU 配方修复了 HDF5 的
MPI Fortran 接口选择，因此保留其独立构建摘要和原始安装前缀。

QE 和 HDF5 的静态库已经链接进这些可执行文件。动态依赖复用系统的
NVHPC 26.3、OpenMPI 5.0.10、FFTW 3.3.10 及操作系统库；所有程序的原始链接检查
均已确认无需安装目录里的私有动态库。因此无需复制 HDF5 的头文件、静态库和 CMake
开发接口。用户的赝势和算例由用户提供。

构建树、源码、测试、日志、工作流元数据和容器文件都保留在交付/项目目录，
不进入公共应用安装目录。

## 用户启动与安装后验收

```sh
module help qe/7.6-auto
# 在对应分区的 Slurm 作业内：
module load qe/7.6-auto
mpirun -np "$SLURM_NTASKS" pw.x -in input.in > output.out
```

模块根据 `SLURM_JOB_PARTITION` 自动选择分区，加载匹配的 MPI/FFTW，并提示版本、
源码摘要和路径。登录节点加载时会先打印帮助再返回未加载状态。
沿用 `/opt/sbatch_examples` 的资源申请与绑核方式。安装后执行原生程序，运行时不需要 SIF。

管理员完成安装后，由有项目计费权限的 `stardust` 提交四分区原生验收：

```sh
bash /home/stardust/sai-hpc-software/deployment/qe-7.6/submit-native-checks.sh
```

该脚本先核对已安装字节，再使用宿主 MPI 检查全部 67 个程序的动态库，以及
一/两 rank 的硅 SCF/力、Gamma 声子和电荷密度；GPU 分区检查 CUDA 实际执行。
作业与结果写入 `/org/org-open-sai/proj-sai-hpc-software/operations/qe-native-<时间>/`。
它不写 `/opt`。目录中的 `native-check.json` 需结合对应 `sacct` 的成功记录判断。

已经完成的最终 SIF 科学验收见
[QE qualification](../../docs/evidence/qe-20261105/README.md)。项目目录已完成四分区的整套
试安装和文件校验；[原生试运行记录](../../docs/evidence/qe-20261105/native-release-stage.json)
中 DSPRHBM、16V100、8V100V0 已通过全部链接和 SCF/PH/PP 检查，4V100 的双卡作业
仍在排队。管理员物理安装尚未执行；项目试安装与最终 `/opt` 验收分别记录。
