# 10e · OpenSSH 升级

> 运维工具「OpenSSH 升级」：多节点 OpenSSH 源码编译升级，具备**失败自愈回滚**，保证升级失败不丢失 SSH 通道。

## 1. 定位与边界

- 面向 SSH 在线节点做 **OpenSSH 源码编译升级**（`sshd`/`ssh` 等二进制 + `/etc/ssh` 配置的整体替换）。
- 唯一远程通道是 SSH，因此升级过程强制「备份 → 预验证 → 看门狗回滚 → 连接实证」四段安全模型。
- **V1 明确不做**：
  - 包管理器（yum/dnf/apt）升级/降级（包安装节点在探测时提示走系统包管理器）。
  - host key 变更或自动生成（升级只备份/恢复，绝不重新生成）。
  - 非 SSH 带外通道/console 联动。
  - 批内并行放宽（仍受 `node.batch_max_count` 约束）。

代码入口：`apps/openssh_upgrade/`。

## 2. 安全模型（失败不丢 SSH）

```
① 预检探测（只读）        ② 编译+预验证（不触碰线上 sshd）
③ 备份 + 看门狗 + 切流    ④ 连接实证（成功解除看门狗 / 失败自动回滚）
```

| 阶段 | 关键动作 |
|------|----------|
| ① 预检探测 | SSH 连通/凭证/锁定；root 或免密 sudo；当前版本（`sshd -V`/`ssh -V`）；二进制路径解析；包归属（包安装直接拒）；托管方式（systemctl/binary）；`sshd_config` 路径与端口；磁盘空间；编译依赖（gcc/make/zlib/openssl/pam） |
| ② 编译+预验证 | 上传源码包 → configure（强制 `--prefix=/usr --sysconfdir=/etc/ssh`，附加参数可配）→ make → `make install DESTDIR=<staging>`（**不直接写系统路径**）；新二进制 `sshd -t -f <config>` 校验现有配置；在备用端口（默认 2222，可设 0 跳过）拉起并用平台同凭证真实连接验证 |
| ③ 备份+看门狗+切流 | 备份全部二进制 + `/etc/ssh`（含 host keys，只备份不覆盖）到 `{openssh.backup_dir}/{hostname}/openssh/{时间戳}/`；生成回滚脚本（`sleep grace` 后无成功标记则恢复备份并重启旧 sshd）；`nohup setsid` 调度；替换二进制 → 特权分离目录 fixup → `systemctl restart sshd`（或二进制方式） |
| ④ 连接实证 | 平台用**全新 SSH 连接**重连 `node.port`；成功 → `sshd -V` 验证新版本 → 写入 OK 标记解除看门狗 → `success`；失败/超时 → 看门狗宽限内自动回滚 → 平台用旧版本重连 → `failed` +「已自动回滚」 |

**关键保证**
- 切换前任意失败：线上 sshd 分毫未动，任务 `failed`，无需回滚。
- 切换后失败：看门狗在宽限期（`openssh.reconnect_grace_seconds`，默认 60s）内自动还原旧版本。
- 当前会话即使被断：命令已发出、看门狗已调度，线程走「等待回滚完成」路径收敛为确定终态。

## 3. 页面与交互

| 页面 | 路由 | 说明 |
|------|------|------|
| 首页（运维台） | `/openssh-upgrade/` | 统计卡（历史/进行中/近 7 天成功/失败回滚）+ 最近任务表；按钮：开始升级 / 源码包 / 历史 |
| 升级中心 | `/openssh-upgrade/center/` | 三步向导：① 选节点+源码包 → ② 探测结果与升级配置（工作目录/预验证端口/回滚宽限/-j/configure 参数/自动回滚开关）→ ③ 确认（断连风险勾选）；右栏 sticky「升级执行进度」批次轮询，不用全局 overlay |
| 历史 | `/openssh-upgrade/history/` | 搜索（节点/IP/版本/批次）+ 状态筛选（含虚拟「进行中」）+ 每页条数 |
| 任务详情 | `/openssh-upgrade/task/<pk>/log/` | 任务信息 + configure 参数 + 完整执行日志（轮询）；成功/失败/已回滚且有备份清单时提供「手动回滚」按钮 |
| 源码包 | `/openssh-upgrade/packages/`、`packages/upload/` | OpenSSH 源码包上传（.tar.gz/.tgz，≤ `openssh.package_max_size_mb`MB）/列表/删除 |

向导节点勾选门禁：`status == online` 且已配置凭证且未锁定（源码编译还需 root 或免密 sudo，由探测阶段判定）。

## 4. 数据模型

- `OpenSSHSourcePackage`：源码包（名称/版本/文件/MD5/描述/上传人），对齐 `NginxSourcePackage`。
- `OpenSSHUpgradeTask`：
  - `action = upgrade | rollback`（**回滚为新建任务**，对齐 Q89 哲学）
  - `batch_number`：升级 `OSI-YYMMDD-NNNN`、回滚 `OSR-YYMMDD-NNNN`（当日自增，事务锁）
  - 运行时字段：`binaries`(JSON) / `is_root` / `use_sudo` / `manage_mode` / `manage_unit` / `sshd_config_path` / `sshd_port` / `sshd_binary` / `home_dir`
  - 回滚材料：`backup_dir` / `backup_manifest_json` / `rollback_script_path` / `ok_marker` / `rolled_back_marker`
  - 状态机：`pending → probing → building → verifying → backing_up → switching → confirming → success | failed | rolled_back | cancelled`
  - 升级成功后回写 `upgraded_openssh_version`，并调用 `apply_openssh_probe_result(node, True, version)` 更新节点。

## 5. 权限

资源 `openssh_upgrade`：`read`（首页/历史/详情）、`create`（发起升级/上传源码包）、`update`（手动回滚）、`delete`。

**授权策略**：仅创建 `PermissionItem` 行（空授权），**不自动迁移** `upgrade.*` 权限（风险最高的操作，需管理员显式勾选授予）。

## 6. 系统设置（PRESET 分组「OpenSSH升级」）

| 键 | 默认 | 说明 |
|----|------|------|
| `openssh.default_work_dir` | `/tmp/openssh-upgrade` | 默认远程编译工作目录 |
| `openssh.backup_dir` | `/opt/app/mascloud/ansible/mngxops/openssh` | 远程备份根目录 |
| `openssh.reconnect_grace_seconds` | `60` | 看门狗回滚宽限（秒），10–600 |
| `openssh.test_port` | `2222` | 预验证备用端口（0=跳过） |
| `openssh.default_configure_opts` | `--prefix=/usr --sysconfdir=/etc/ssh --with-pam` | 默认 configure 参数（prefix/sysconfdir 固定） |
| `openssh.package_max_size_mb` | `20` | 源码包上传大小上限 |
| `system.retention_openssh_task_days` | `90` | OpenSSH 任务保留天数（0=不清理） |

## 7. 任务中心集成

- `operation_type = openssh_upgrade`（任务中心筛选下拉可见）。
- 摘要主行=批次号（`OSI-`/`OSR-`），副行=成功/失败计数（`task_result.format_task_center_summary`）。
- 任务详情入口链 `openssh_upgrade:task_log`；来源批次链 `openssh_upgrade:history?search=<batch>`。
- 协作取消：仅 `pending/probing/building/verifying` 阶段可安全取消；进入备份后完成当前安全流程再终态。取消级联将进行中明细置 `failed`。
- 启动清理（`startup_cleanup`）将遗留非终态标 `failed`（Q161 先例）。
- 数据保留：`system.retention_openssh_task_days` 清理，跳过进行中阶段。

## 8. 节点集成

- `Node.openssh_version` / `Node.last_openssh_probe_at`：
  - 升级成功后回写；
  - 各 SSH 测活路径（节点单测/批测/解锁、系统信息采集、Nginx 版本检测、凭证启用测试）连接成功时同步探测 `sshd -V`/`ssh -V` 并回写（探测失败不清除已有版本）。
- 节点列表新增「OpenSSH」徽标列，详情弹窗展示版本与探测时间。
- `utils/ssh.get_openssh_version()`：探测远程 OpenSSH 版本。
- `apps/nodes/services.apply_openssh_probe_result()`：统一写入 OpenSSH 探测结果。

## 9. 批次前缀约定

| 模块 | 升级 | 回滚 |
|------|------|------|
| OpenSSH | `OSI-YYMMDD-NNNN` | `OSR-YYMMDD-NNNN` |
| Nginx 升级 | `UG-` | — |
| Nginx 安装 | `IN-` | — |
| Nginx 启停 | `OP-` | — |
| Nginx 卸载 | `UN-` | — |
