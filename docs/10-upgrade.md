# 10 · Nginx 编译升级（upgrade）

## 1. 模块目标与范围

上传/管理 Nginx 源码包与第三方模块离线包；四步升级向导对多节点远程编译安装；升级历史与任务日志；可选回滚入口。

**不做**：官方 apt/yum 包升级编排；容器镜像构建；平台代拉 Git 再转发。

**节点门禁（Q150）**：升级选节点要求 online + 凭证 + `nginx_available=True`；无 Nginx 时禁选并引导「运维工具 → Nginx 安装」。

## 2. 角色与权限

`upgrade.read|create|update|delete`。

## 3. 领域模型

[`apps/upgrade/models.py`](../apps/upgrade/models.py)

### NginxSourcePackage

`version`+`uploaded_by` 唯一；`package_file`、`file_md5`、`file_size`、`description`、`is_official`、`custom_modules_included` JSON。

同版本冲突：预检 + showConfirm 覆盖（Q62）。  
源码包列表展示 `description`（Q118）。

### NginxThirdPartyModulePackage（Q120）

`name`+`version`+`uploaded_by` 唯一；`package_file`（`.tar.gz` / `.tgz` / `.zip`）、`file_md5`、`file_size`、`description`。  
模块名即远程目录名，用于 `--add-module={work_dir}/nginx-modules/{name}`。

### NginxUpgradeTask

| 方面 | 内容 |
|------|------|
| 批次 | `UG-YYMMDD-XXXX` |
| 模式 | install / upgrade / switch_path |
| 参数 | current_* / target_* configure、modules JSON |
| 第三方 | `added_third_party` JSON：在线 `source=git`+`git_url`+`branch`；离线 `source=package`+`package_id`（兼容仅有 `git_url`） |
| 编译 | `make_jobs`、`remote_work_dir` |
| 状态 | pending → fetching_config → uploading… → downloading_modules → configuring → compiling → backing_up → replacing_binary → upgrading → verifying → success/failed/rollback/cancelled |
| 关联 | `task_center` FK |

## 4. 页面与路由

| 功能 | 路径 |
|------|------|
| 运维台首页 | `/upgrade/`（任务列表风，Q60） |
| 源码包 | `/upgrade/packages/` 上传/校验/删除/下载；列表含描述列（Q118） |
| 第三方模块包 | `/upgrade/modules/` 上传/校验/删除/下载（Q120） |
| 升级中心 | `/upgrade/center/` |
| API | nginx-v / parse-config / compute-config / batch-progress |
| 任务 | create / progress / log / cancel / rollback |
| 历史 | `/upgrade/history/`（支持 running 过滤） |

## 5. 升级中心四步向导（Q64/Q65）

1. **选节点 + 源码包**：节点弹窗多选 + tag 搜索；包列表 data-table + 过滤。  
2. **编译环境**：拉取各节点 `nginx -V`（`NginxVApiView` / `fetch_nginx_v_from_node`）；先写 current_version（Q63）。  
3. **编译参数**：模块左右栏；`BUILTIN_ADD_MODULES` 对齐官方 options（Q67）；参数差异弹窗；configure 引号感知分词拼接防截断（Q65）；第三方模块支持 **在线 Git / 离线包** 双通道（Q120）。左侧列出全部官方参数，节点已启用项以「已编译」禁用态展示仍可搜索命中；右侧为当前 `nginx -V` 参数可勾选移除（Q121）；「已编译」判定精确匹配，不含静态/`=dynamic` 模糊等价（Q122）。  
4. **确认**：最终参数批注；showConfirm；创建批次并行升级。

高级项默认：`upgrade.default_work_dir`、`upgrade.make_jobs_default`（页面以服务端默认渲染，Q81）；**再进入向导一律从第 1 步空白开始**，不恢复节点/包/参数草稿；仅续看进行中批次进度（Q113）。

## 6. 执行流水线

实现：[`apps/upgrade/services.py`](../apps/upgrade/services.py) `run_upgrade_task`。

典型步骤：

1. 工具预检（gcc/make 等，Q63）  
2. 上传源码包到 `remote_work_dir`  
3. 解压  
4. **准备第三方模块**（Q120）：  
   - 在线：目录不存在则 `git clone`；已存在则校正分支（checkout）后 `git pull`  
   - 离线：平台 SFTP 下发压缩包并解压到 `nginx-modules/{name}`（已有目录先删再解压）  
5. 备份旧二进制  
6. configure（安全 join；补齐 `--add-module`）、`make -j`  
7. `make install`  
8. `nginx -t`  
9. `reload_nginx`（`utils/nginx_ops.py`，按 pid-path/启动方式）  
10. 验证版本，回写 `Node.nginx_version`  

同步更新 `NginxUpgradeTask` 与关联 `TaskCenterTask(nginx_upgrade)`；日志 strip + 前端 2s 轮询 task_log（Q65）。  
上传进度：XHR `upload.onprogress` + overlay（Q62）。  
编译错误：按 exit code 合并 stdout/stderr，避免被无关输出掩盖（Q63）。

## 7. 实现要点

| 能力 | 路径 |
|------|------|
| 服务 | `apps/upgrade/services.py` |
| 视图 | `apps/upgrade/views.py` |
| 内置模块列表 | 主要在 `center.html` / 相关常量 |
| 包大小限制 | `upgrade.package_max_size_mb`（源码包与第三方模块包共用；默认 20MB；label 已标明双通道） |

## 8. 前后端约定

- 开跑后按钮仍可点时警告将产生新批次（Q65）。  
- 多节点参数不一致：签名对比 + 差异可展开。  
- 版本列展示纯版本号（Q63）。  
- 第三方模块 JSON：`{"name","source":"git","git_url","branch"}` 或 `{"name","source":"package","package_id"}`。

## 9. 异常与边界

- 取消：`cancelled` 状态（在途 SSH 能否立即中断以实现为准）。  
- 回滚：`UpgradeTaskRollbackView` / `nginx_rollback` 类型——能力边界以实现为准。  
- 工作目录更改后须按 Q81 规则恢复表单，避免仍用旧默认。  
- 在线 Git 失败且疑似无网/无 git：错误信息提示改用离线包（Q120）。

## 10. 关联模块

nodes、task center、settings、audit、nginx_ops。

## 11. 已落地优化索引

Q60–Q65、Q67、Q81、Q113、Q118、Q120、Q121、Q122。

## 12. 待确认缺口

无单独新编号。
