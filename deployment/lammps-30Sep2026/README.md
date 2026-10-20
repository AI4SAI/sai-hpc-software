# LAMMPS 30 Sep 2026 / DeePMD 3.2.0

本目录交付 4V100、16V100、8V100V0 三个 GPU 分区的已验收产物。
每份均包含 LAMMPS 和配套 DeePMD，保留 GPU package、Kokkos CUDA/SM70、
MPI/OpenMP、PLUMED 2.10.1，以及 TF/PT/JAX 后端。
站点 PyTorch 后端使用 CUDA；TF/JAX 继续保留其数值正确性验收。
性能验收范围是 PyTorch GPU，不包含纯 CPU 性能比较。

服务器准备目录：

```text
/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026
```

## 直接使用已验收的容器

在这三个分区之一的 Slurm allocation 内加载：

```bash
module use /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/modulefiles
module load lammps/30Sep2026-deepmd320-gpu
lmp -h
```

这个 module 根据 `SLURM_JOB_PARTITION` 选择该分区的已发布镜像和启动器。
运行 `lmp` 即调用容器中的程序，外部 MPI/CUDA 继续使用站点 module。
GPU package 可用 `-sf gpu -pk gpu 1`；Kokkos 可用
`-k on g 1 t 1 -sf kk -pk kokkos gpu/aware off`。
具体模型需有对应加速 style；DeePMD 的 PyTorch CUDA 路径不需要这两个 suffix。

## 安装到物理 /opt

`release.json` 固定三个已发布镜像、发布记录、六个配套安装目录，以及全部清单的 SHA256。
导出目录中的内容按原样部署，保留精确编译前缀。DeePMD 和 LAMMPS 必须一起安装，
不能只复制 `lmp`。GCC、MPI、CUDA、PLUMED 和后端环境继续引用已有站点依赖。

默认只做只读预检，可由能读取源文件的账号执行：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/install.sh --check
```

由具有目标 `/opt/apps` 和 module 目录写权限的账号执行安装：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/install.sh --install
```

脚本校验源 SIF、发布记录和每个原生文件；先复制到目标文件系统的隐藏暂存目录，
回读校验全部文件、权限和符号链接，再更名为最终目录。已有相同安装可重入，
不同内容会报错，失败的暂存目录保留。六个目录全部就绪后才发布：

```text
/opt/modules/modulefiles/apps/lammps/30Sep2026-deepmd320-auto
```

本次只准备材料和执行预检，尚未写入物理 `/opt`。
安装后的字节校验：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026/install.sh --verify
```

随后可在对应 Slurm allocation 内加载 `lammps/30Sep2026-deepmd320-auto`。
物理安装后的实际计算验收需要在管理员完成复制后执行；容器验收和字节校验
不能替代这一检查。安装不会更改旧版 module 或默认版本。

完整验收来源见 [LAMMPS-RELEASE.md](../../docs/LAMMPS-RELEASE.md)。
