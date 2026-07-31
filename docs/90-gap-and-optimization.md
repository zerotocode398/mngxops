# 90 · 缺失需求与设计优化清单

本文档与 [`AGENTS.md`](../AGENTS.md) **Q84–Q118** 双向索引。  
2026-07-30：未写入状态相关 UI 下线、小缺陷修复；产品结论项关闭；技术债保留兼容。  
2026-07-31 增补 Q114–Q116：离线门禁、失败原因多行、无权访问弹窗。  
2026-07-31 增补 Q117：权限矩阵勾选与全选按钮。  
2026-07-31 增补 Q118：源码包列表展示描述。

需求基线见 [README.md](README.md)。

---

## 总览（落地后）

| 编号 | 摘要 | 状态 | 落地要点 |
|------|------|------|----------|
| Q84 | `conflict` 无写入 | 已完成 | 下线过滤/仪表盘/发布中心展示 |
| Q85 | `syncing` 无写入 | 已完成 | 同上 |
| Q86 | 漂移检测 | 已关闭（结论） | 不做；筛选隐藏类型 |
| Q87 | 任务类型不一致 | 已完成 | 下拉隐藏未用类型 + 代码注释 |
| Q88 | 列表/详情权限 | 已完成 | 统一 `node_batch_test`+`config_batch_sync` |
| Q89 | rollback 状态闲置 | 已关闭（结论） | 维持「回滚=新任务」 |
| Q90 | 待删可发布 | 已完成 | API/统计排除 `marked_deleted` |
| Q91 | 恢复标 synced | 已完成 | 恢复一律 `modified` |
| Q92 | Glob 多节点 | 已完成 | 仅单节点，多选 400 |
| Q93 | 全局 running 门禁 | 已关闭（结论） | 维持并文档化 |
| Q94 | 审计多为 success | 已关闭（结论） | 口径=落库成功变更 |
| Q95 | ConfigVersion 双路由 | 已关闭（结论） | 保留兼容；正式入口用 bindings 路由 |
| Q114 | 离线禁止同步/发布 | 已完成 | 仅 `status==online`；对齐升级中心 |
| Q115 | 失败原因多行展示 | 已完成 | 详情将管道折叠还原为多行 |
| Q116 | 无权访问弹窗 | 已完成 | 回首页 + `showAlert`；首页入口按权限 |
| Q117 | 权限矩阵勾选 | 已完成 | 空集合 every 误判 + PermissionItem 启动 seed |
| Q118 | 源码包列表描述 | 已完成 | `package_list` 增加描述列 |

---

## 明细（结论摘要）

### Q84 / Q85 — 已完成

下线配置列表「冲突/同步中」过滤标签与节点头汇总；发布中心对应过滤；仪表盘冲突卡。model choices 与行内兜底 badge 保留。

### Q86 — 已关闭（结论）

不做巡检；`remote_content_hash` 仍可由发布写入；任务筛选不展示漂移类型。

### Q87 — 已完成

发现/同步统一 `config_batch_sync`；Glob 仍为同步 HTTP；历史枚举仅兼容展示。

### Q88 — 已完成

见 `TaskCenterListView` / `TaskCenterDetailView`。

### Q89 — 已关闭（结论）

回滚继续创建新 `ReleaseTask`；不回写源任务 `rollback` 状态。

### Q90 — 已完成

`ReleaseNodeBindingsAPIView` 与 `_build_release_status_counts` 排除待删。

### Q91 — 已完成

`BindingVersionRestoreView` → `modified` + 文案提示需再发布。

### Q92 — 已完成

`ConfigGlobPreviewView` 多节点拒绝。

### Q93 — 已关闭（结论）

任意 `ReleaseTask.status=running` 时拒绝新批次自动执行；见 [08-releases](08-releases.md)、[16-nfr](16-nfr.md)。

### Q94 — 已关闭（结论）

CRUD 信号审计表示「变更已落库」；不保证覆盖所有业务失败路径。

### Q95 — 已关闭（结论）

正式版本历史：`/configs/bindings/<pk>/versions/`；旧路由与 `ConfigVersion` 保留兼容并标明废弃。

### Q114 — 已完成

仅 `Node.status == "online"` 可同步/发布/回滚（`offline`/`unknown` 禁止），文案对齐升级中心「非在线状态」。前后端双门禁；纯本地 CRUD 不受影响。

### Q115 — 已完成

任务详情执行结果：失败项拆 label / 失败原因，将存储协议中的 ` | ` 折叠还原为多行；不改 `TaskCenterTask.result` 写入格式。

### Q116 — 已完成

页面无权访问：session 标记后回首页，全局 `showAlert` 提示「无访问权限」；AJAX 仍 JSON 403。首页快捷入口与统计卡跳转按权限收口。

### Q117 — 已完成

权限矩阵：`allChecked` 排除空集合误判；`initPermMatrix` 全局化 + 单元格点击；`UsersConfig.ready` 补齐 `PermissionItem`。

### Q118 — 已完成

源码包管理列表展示上传时填写的 `description`（截断 + 悬停全文）。

---

## 维护规则

1. 新增缺口续编 **Q119+**，同步改 AGENTS 与本文件。  
2. 需求正文（02–12）描述**已实现**行为；关闭项记入上表即可。
