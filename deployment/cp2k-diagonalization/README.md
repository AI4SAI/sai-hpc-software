# CP2K 2026.2 PBE 对角化试验

对应 [issue #9](https://github.com/AI4SAI/sai-hpc-software/issues/9)。使用同一已通过科学验收的
release SIF、同一 PBE 体系/基组/截断/精度，比较 cuSOLVER 的
`DIRECT_GENERALIZED_DIAGONALIZATION F/T`，并保留 ELPA 和 OT 对照。
不修改软件默认值。运行在一个节点、一个 MPI rank、一张 V100 上，OpenMP 使用所分配
的物理核数（16V100/4V100 四核，8V100V0 三核）。

```bash
python3 deployment/cp2k-diagonalization/run.py submit \
  --build-run <已通过科学验收的构建run> --name <唯一标识> --edge 6 --repeats 3
```

`--edge 4` 为 64 个水分子，`--edge 6` 为 216 个水分子。坐标按与现有 water64 验收
相同的晶格生成，后者矩阵为 4968 阶；不是用户生产体系或上游参考能量。
四组均使用 PBE、DZVP-MOLOPT-SR-GTH、300 Ry 和 `EPS_SCF 1e-9`。
cuSOLVER F/T 的输入只有该开关不同；显式使用 `CHOLESKY RESTORE`，不加 level shift。
每组先预热一次，再按正序/倒序交替重复三次。每次从相同原子初猜独立开始。

## OT 内部的求解器对照

要判断 cuSOLVERMp 是否加速 OT，使用独立的 `ot-backends` profile：

```bash
python3 deployment/cp2k-diagonalization/run.py submit \
  --build-run <已通过科学验收的构建run> --name <唯一标识>-ot-water216 \
  --edge 6 --repeats 3 --profile ot-backends
```

两组都保留 OT/DIIS、`PRECONDITIONER FULL_ALL` 和相同的收敛参数，
只改变 `PREFERRED_DIAG_LIBRARY CUSOLVER/ELPA`。两组的
`DIRECT_GENERALIZED_DIAGONALIZATION` 均为 `F`：OT 中的预条件器和轨道矩阵
操作会调用普通特征值求解器，该 direct 开关不改变这些调用。
验证器要求实际执行 OT 及指定的求解器，拒绝回落到另一后端或 generalized direct 路径。
每组一次预热、三次交替测量，共八次计算；报告 `elpa_over_cusolver` 时间比，
大于 1 才表示该 OT 算例中 cuSOLVERMp 更快。它不代表所有 OT 输入都会调用或受益于同一后端。
默认 `diagonalization` profile 保持原来的四组对照；其中 OT 与普通对角化的总时间
比较不能回答 OT 内部更换求解器的收益。

## 验证和结果

判定使用 `eigensolver_generalized` 和 `cp_fm_general_cusolver` 的真实调用计数，不能仅靠
输入开关判断 direct 分支是否执行。普通 cuSOLVER、ELPA、OT 同样需要实际调用计时。
所有能量、力、SCF 收敛、CPU 亲和性和 GPU 可见性先通过检查，才汇总 CP2K 内部总时间。
同时记录 1 秒采样的 GPU 使用率/显存等；这是整个分配 GPU 的采样，不是精确的单进程峰值。
启动器的镜像校验开销只计入单独的 elapsed，不混入 CP2K 内部总时间。

结果全部位于 `/home/stardust/sai-hpc-software/runtime-tests/cp2k-direct-*`；MPI/容器临时
目录和缓存均在项目家目录。作业结束后保存 sacct 六列记录并独立校验：

```bash
sacct -X -n -P -j <job> --format=JobIDRaw,Partition,State,ExitCode,Elapsed,NodeList \
  > <结果目录>/results/accounting.txt
python3 deployment/cp2k-diagonalization/run.py verify <结果目录>
```

自动等待指定工作流的科学验收后提交 216 水分子测量：

```bash
python3 deployment/cp2k-diagonalization/watch.py <workflow_id> \
  --recipe <完整recipe哈希> --targets 16v100-avx2 4v100-avx512 8v100v0-avx512
```

watcher 固定工作流、recipe 和 source 身份，只提交已通过科学验收的候选。
存在尚未确认的提交目录时停止，避免重复提交。工作流自身继续负责四分区编译、科学验收和发布。
试验结果只适用于记录的体系和资源，不用于推断所有大体系或多 GPU 的收益。
