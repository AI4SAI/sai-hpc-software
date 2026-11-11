# Lmod 全局缓存维护

此入口独立于软件安装，使用已安装的 Lmod 9.2.4 官方
`update_lmod_system_cache_files`，重建系统 Lua/编译缓存并发布配套时间戳。
扫描 `/etc/lmod/.modulespath` 中现有的五个公共模块根目录：apps、devtools、
compiler、mathlib、mpi；不把用户临时 `module use` 的路径加入全局缓存。

```bash
bash /home/stardust/sai-hpc-software/deployment/lmod-cache/refresh.sh --check
bash /home/stardust/sai-hpc-software/deployment/lmod-cache/refresh.sh --refresh
```

截至 2026-11-12，全局目录 `/opt/modules/lmod/cache` 属于 `entropy:entropy`，
模式为 0775，无额外 ACL；旧缓存备份为 0644。`saiuser01` 不属于 `entropy`
组，因此目前不能运行刷新。上述命令应由 `entropy` 或 root 执行；若长期委托
`saiuser01`，先由现有管理员授予缓存目录及已有备份文件所需写权限。`--check`
会检查当前执行账号的实际权限，未获授权时不会改动缓存。

官方工具保留上一份缓存，生成新缓存后再更新时间戳。不要只触碰时间戳，
也无需删除其他用户家目录。正常刷新后可检查：

```bash
module avail gpumdkit neptrainkit
module whatis gpumdkit/1.5.9 neptrainkit/3.1.5
```

本包只提供维护入口，不自动授予权限、不修改 Lmod 配置、不创建定时任务。
首次核查时，缓存仍为 11 月 9 日版本；普通 `avail` 查不到这两个已安装模块，
加 `--ignore_cache` 则能查到。没有写权限时可用该参数临时查询、加载当前模块。

已用同一官方工具在项目目录生成独立缓存，确认五个真实公共模块根目录能完成
扫描、生成 Lua 和编译缓存，并收录 NepTrainKit、GPUMDkit 和 LAMMPS update1。
这个验证没有更新全局缓存；正式刷新仍需上述维护权限。
