# LAMMPS 30Sep2026 update1 原生安装

将已通过科学验收的三个 SIF 中的标准 LAMMPS 安装文件导出到 `/opt`。
源码为 `stable_30Sep2026_update1`，提交
`098ffdefa525cabc82272925de02e9884f58c657`。支持 `4V100`、`16V100`、
`8V100V0`，复用这些分区已有的 DeePMD 3.2.0 和集群编译器、MPI、CUDA、PLUMED。

## 管理员安装

交付包放在 stardust 家目录；安装不需要读取 `/org` 的构建目录。
以有 `/opt/apps/lammps` 和 LAMMPS module 目录写权限的管理员账号执行：

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/install.sh --check
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/install.sh --install
bash /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/install.sh --verify
```

`--check` 只读核对 SIF、科学验收记录及已安装 DeePMD 的文件哈希；
`--install` 自动先执行相同检查，安装后再次校验。
同一份已安装内容可重复执行；发现不同内容会停止。
最后出现 `LAMMPS_UPDATE1_INSTALLED` 和 `LAMMPS_UPDATE1_VERIFIED` 表示安装及文件校验成功。
三个分区总计约 **2.81 GiB**，每个分区 340 个普通文件、一个库软链接。
安装只解压和复制，无需重新编译。

## 目录与 module

```text
/opt/apps/lammps/
└── lammps-release-stable_30Sep2026_update1-g098ffdefa525-rb6a6aadddb8b/
    ├── 4V100/
    ├── 16V100/
    └── 8V100V0/
        ├── bin/lmp
        ├── lib/liblammps.so -> liblammps.so.0
        ├── lib/liblammps.so.0
        ├── lib/cmake/LAMMPS/
        ├── lib/pkgconfig/liblammps.pc
        ├── include/lammps/
        ├── etc/profile.d/lammps.{sh,csh}
        ├── share/lammps/potentials/  (287 个势文件)
        └── share/man/man1/lmp.1

/opt/modules/modulefiles/apps/lammps/30Sep2026_update1-deepmd320-auto
```

三个分区具有相同目录结构，文件针对对应 CPU/MPI 配置编译。
`/opt/apps` 中只保留标准程序、库、头文件、构建接口、势文件和手册；
验收脚本、日志、工作流元数据、基准算例均保留在交付包或项目目录。

在计算作业里使用：

```bash
module load lammps/30Sep2026_update1-deepmd320-auto
mpirun lmp -in in.lammps
```

module 根据 Slurm 分区选择原生程序，使用宿主 MPI，运行时不启动 Apptainer。
保留 MPI/OpenMP、GPU package、KOKKOS CUDA SM70、DeePMD、PLUMED、Python、
REAXFF、ML-IAP/ML-SNAP 等原有功能；完整清单共 71 个 packages、2473 个 styles。
GPU/Kokkos 加速仍按算例原有 LAMMPS 参数选择。

登录节点无需加载 module 即可查看：

```bash
module --ignore_cache whatis lammps/30Sep2026_update1-deepmd320-auto
module --ignore_cache help lammps/30Sep2026_update1-deepmd320-auto
cat /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/build-info/index.txt
```

索引给出各分区实际安装路径、势文件路径，以及已保存的完整 `lmp -h` 输出文件。
`module-whatis` 只有一条，包含 Features；登录节点加载被拒绝，但 help/whatis 可用。

原 `lammps/30Sep2026-deepmd320-auto` 继续保留。
现有 NEPAdapters 1.0.4/1.0.5 module 明确依赖原版 LAMMPS，
与 update1 的组合尚未验收，请继续通过原有 NEPAdapters module 使用。

## 安装后的原生验收

文件校验不替代计算验收。管理员安装完成后，由 stardust 在项目账号提交：

```bash
python3 -B /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/native_checks.py submit
```

命令打印三个作业号及 `Results:` 目录。作业完成后执行：

```text
python3 -B /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/native_checks.py verify <Results目录>
```

每个分区申请两节点、每节点一张 GPU，使用宿主 MPI 直接启动原生 `lmp`。
检查版本、动态库、71 packages/2473 styles、模块卸载，以及 14 个科学算例：
TensorFlow/PyTorch/JAX DeePMD + PLUMED 单/双节点，GPU/Kokkos LJ 与 Coulomb/PPPM
单/双节点。结果与原已验收 SIF 的数值在相同容差下比较。
任务、输入、原始输出均保存在 `/org/org-open-sai/proj-sai-hpc-software/operations`，
不会写入 `/opt`。

`--stage-root` 供维护者在项目目录试安装；该路线的报告标注 `staged-native`。
正式 `/opt` 验收标注 `physical-opt`，两者分别记录。
