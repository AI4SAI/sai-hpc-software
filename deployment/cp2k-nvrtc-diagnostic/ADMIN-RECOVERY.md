# NVRTC 客户端缓存异常：管理员检查与恢复

已复现的故障是 `4v100n06` 读取 NVRTC 库时，普通读取和绕过客户端页缓存的
`O_DIRECT` 读取内容不一致。`/opt/devtools` 挂载的是
`sai-apprepo:/opt/devtools`，协议为 NFS 4.2 / RDMA；`/home` 是另外的 Lustre
文件系统。当前证据不支持覆盖共享库文件。

已知正常 SHA-256：

```text
c3430e8b6b0d8d3a74f5f839118a85e13e3ad6e4d6203ea1b6a27a0c0b4fe10a
```

异常节点普通读取的 SHA-256：

```text
7903a559640531e1859c53b4d8e0327bd037418fdba3afe04d636dba040ea40a
```

两者文件长度均为 106237008 字节。不能只检查文件名、版本、大小或 ELF build ID。

## 只读检查

在有计算节点登录权限的管理员账号下执行；普通用户需要先获得自己的 Slurm 分配。
以下命令的输出写在执行账号的家目录，不改软件文件、不刷新缓存。

```bash
report_dir="$HOME/cp2k-nvrtc-check-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$report_dir"
probe=/home/stardust/sai-hpc-software/development/workflow-closure/deployment/cp2k-nvrtc-diagnostic/storage_probe.py
ssh 4v100n06 python3 -B "$probe" > "$report_dir/4v100n06.json"
```

`storage_probe.py` 对 toolkit 和 HPC SDK 中的 NVRTC 分别执行：普通读取、直接读取、
再次普通读取；记录挂载、inode、大小、mtime/ctime、完整字节数、校验值和每次读取的
退出码。脚本退出成功仅表示完成采集，判断健康必须检查 JSON 中每次读取。

如要扩大到全部节点，可在有权限的账号下保存节点清单并逐个检查；以下示例限制在
四个 CP2K 部署分区，只做只读采集：

```bash
sinfo -N -h -p 4V100,16V100,8V100V0,DSPRHBM -o '%N' | sort -u > "$report_dir/nodes.txt"
while IFS= read -r node; do
    if timeout 30 ssh -o BatchMode=yes -o ConnectTimeout=8 "$node" \
        python3 -B "$probe" > "$report_dir/$node.json" 2> "$report_dir/$node.stderr"; then
        printf '%s collected\n' "$node"
    else
        printf '%s unavailable; inspect stderr\n' "$node"
    fi
done < "$report_dir/nodes.txt"
```

连接失败、JSON 不完整、文件缺失、直接读取不支持，都不能算“通过”。使用已配置的
管理员 SSH 主机密钥信任关系；脚本不会绕过登录或主机密钥验证。这个步骤不检查
整个文件系统的完整性，只检查指定的两个 NVRTC 文件。

存储管理员也可以直接在 `sai-apprepo` 上校验导出中的同一文件及底层存储日志。
`O_DIRECT` 绕过客户端页缓存，但不代表绕过服务端缓存，更不能代替介质健康检查。

## 恢复顺序

1. 将已确认异常的节点停止接收新作业，并按站点维护流程处理现有作业。保留上述
   JSON 及故障日志，记录节点启动时间、内核版本、NFS/RDMA 配置，以及已有的
   EDAC/MCE、内核和 RDMA 错误记录。
2. 节点上相关进程退出后，按站点流程使异常客户端缓存失效，必要时重新挂载或重启
   该节点。清理缓存只能消除当前异常状态，不能证明其成因已经解决。当前记录不能
   区分内存、内核/NFS 客户端、先前 RDMA 传输或其他原因。
3. 在原节点、原 `/opt` 路径复查普通/直接/普通三次读取，必须全部得到正常校验值，
   文件身份和时间戳也应保持一致。两份 NVRTC 库都要检查。
4. 在 GPU Slurm 分配中加载原始 `cp2k/2026.2-auto`，先运行独立 NVRTC 编译探针，
   再运行已有 CP2K GPU 冒烟测试。验收时不使用家目录替代库，不设置
   `DBCSR_RUN_ON_GPU=0`，确保验证的是正常生产路径。
5. 普通读取、直接读取、GPU 即时编译、CP2K 的退出状态和数值验收全部通过后，再
   恢复节点派发，并继续观察是否复发。

独立探针使用方法见 [README.md](README.md)，科学对照与原始证据见
[诊断记录](../../docs/evidence/cp2k-nvrtc-node-20261021/README.md)。此次排查没有执行
节点 drain、缓存清理、重新挂载、重启或共享文件替换；这些由管理员安排维护窗口。
