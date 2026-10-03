# Nginx 编译升级

## 页面与权限

- `/upgrade/` 跳转到升级中心 `/upgrade/center/`；包管理位于 `/upgrade/packages/` 和 `/upgrade/modules/`，升级历史位于 `/upgrade/history/`，单个任务详情位于 `/upgrade/tasks/{task_id}/`。
- 升级向导分为目标选择、编译环境、编译参数和确认执行四步。每次进入向导均从空白状态开始；只有当前打开的向导保留本次节点基线与选择。
- 页面和接口使用 `upgrade.read`、`upgrade.create`、`upgrade.delete`、`upgrade.execute`。升级、取消和回滚任务受统一会话、CSRF、RBAC 与任务本人可见范围保护。
- 一批最多选择 `node.batch_max_count` 个节点，默认 3。目标必须未删除、未锁定、SSH 在线、已配置启用凭证且 `nginx_available=true`；门禁在创建任务和任务执行前分别检查。

## 包管理

- 源码包只接受 `.tar.gz` / `.tgz`；第三方模块离线包接受 `.tar.gz` / `.tgz` / `.zip`。默认上传上限为 20 MiB，由 `upgrade.package_max_size_mb` 控制（1 到 2048 MiB），保存后立即生效。
- 包文件保存在 `NGXOPS_HOME/nginx_packages/`，数据库只保存文件名、版本、大小、MD5、描述和上传人。归档在落盘前检查成员路径、链接/特殊文件类型、成员数和 2 GiB 展开体积；源码包版本可从文件名 `nginx-{version}` 推断。
- 相同上传人和源码版本、或相同上传人/模块名/版本会触发覆盖确认；相同文件内容也会提示确认。覆盖和删除会拒绝仍被 pending/running 升级或全新安装任务引用的包，并与两类批次创建共用进程内锁串行化。
- 归档通过临时唯一文件名写入，数据库提交成功后才移除旧归档。删除先提交记录，再删除对应文件。应用不会自动清理孤立归档。

## 向导和执行

1. 选择符合门禁的节点和平台托管源码包；支持列表关键词筛选。
2. 对每个节点读取 `nginx -V`，展示版本、prefix、二进制位置、内置和第三方 configure 参数。执行前重新读取，并拒绝与向导基线不一致的节点。
3. 选择要增加或移除的官方 configure 参数，并添加在线 Git 或平台离线包模块。参数使用 shell 引号解析和序列化。平滑升级保留节点原 prefix；切换路径升级由服务端将 `--prefix` 和显式 `--sbin-path` 改写到指定安装目录。
4. 检查目标清单后创建按节点分开的 `nginx_upgrade` 任务，共用 `UG-YYMMDD-XXXX` 批次号；通过统一任务 API 轮询实际进度、阶段、日志和结果。

每个任务连接一个节点并复用同一 SSH 会话：检查 gcc/make，上传并校验源码包，解压并准备第三方模块，备份旧二进制，执行 configure/make/make install、`nginx -t`、reload/start，最后读取版本并更新节点资产。Git 获取失败会提示改用离线包。任务异常消息只保留受控摘要和有限命令输出，不记录 SSH 凭证。

取消仅允许 pending、读取配置和源码包上传阶段。执行器对远程命令使用检查点和连接关闭回调；已发出的远程编译命令不保证立即停止。失败任务若已有旧二进制备份且尚未回滚，可创建 `nginx_rollback` 任务；成功回滚后原记录保存回滚时间。

## API

- `POST /api/upgrade/parse-config`：解析 `nginx -V` 输出。
- `POST /api/upgrade/compute-config`：计算内置/第三方模块参数，可按切换路径模式返回最终 prefix 参数。
- `POST /api/upgrade/tasks`：全量校验并创建批次。
- `POST /api/upgrade/tasks/{task_id}/cancel`：取消可取消阶段的升级任务。
- `POST /api/upgrade/tasks/{task_id}/rollback`：创建二进制回滚任务。
- `POST /api/upgrade/nodes/{node_id}/nginx-v`：通过 SSH 读取节点编译基线。
- `GET /api/upgrade/packages/check`、`GET /api/upgrade/modules/check`：检查当前用户的版本冲突。
- 任务状态和日志统一读取 `GET /api/tasks/{task_id}`；受限任务可见性、日志游标和协作取消行为见 [tasks.md](tasks.md)。

页面错误使用重定向或上传结果提示；JSON API 使用公共 `ApiError` 错误结构。接口参数、权限和错误状态登记在 [api.md](api.md)。数据库当前迁移 v14 需要显式运行 `python -m ngxops.database upgrade`；部署前备份数据库、密钥和 `nginx_packages/` 目录。

## 已知边界

- 执行器在单个 Uvicorn worker 的进程内运行；多 worker 部署前需要持久化队列或外部任务执行器。
- configure 差异按参数 token 精确比较，不把 `=dynamic` 与静态模块视为等价。
- 第三方在线 Git 仓库由目标节点直接访问；HTTP(S) URL 不允许嵌入账号或密码，SSH 格式 URL 仍按 Git 的远端认证配置工作。
- `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` 仅在 v14 系统设置表不可用时作为兼容默认值；正常运行时由 `upgrade.package_max_size_mb` 和 `node.batch_max_count` 控制上传大小与批量节点数，见 [settings.md](settings.md)。
