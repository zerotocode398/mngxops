# 90 · 缺失需求与设计优化清单

本文档与 [`AGENTS.md`](../AGENTS.md) **Q84–Q95** 双向索引。  
2026-07-30 已按建议落地：误导 UI 下线、小缺陷修复；产品结论项关闭；技术债延后。

需求基线见 [README.md](README.md)。

---

## 总览（落地后）

| 编号 | 摘要 | 状态 | 落地要点 |
|------|------|------|----------|
| Q84 | `conflict` 无写入 | 已完成 | 下线过滤/仪表盘/发布中心展示 |
| Q85 | `syncing` 无写入 | 已完成 | 同上 |
| Q86 | 漂移检测 | 已关闭（结论） | 现阶段不做；筛选隐藏类型 |
| Q87 | 任务类型不一致 | 已完成 | 下拉隐藏未用类型 + 代码注释 |
| Q88 | 列表/详情权限 | 已完成 | 统一 `node_batch_test`+`config_batch_sync` |
| Q89 | rollback 状态闲置 | 已关闭（结论） | 维持「回滚=新任务」 |
| Q90 | 待删可发布 | 已完成 | API/统计排除 `marked_deleted` |
| Q91 | 恢复标 synced | 已完成 | 恢复一律 `modified` |
| Q92 | Glob 多节点 | 已完成 | 仅单节点，多选 400 |
| Q93 | 全局 running 门禁 | 已关闭（结论） | 维持并文档化 |
| Q94 | 审计多为 success | 已关闭（结论） | 口径=落库成功变更 |
| Q95 | ConfigVersion 双路由 | 已关闭（结论） | 延后清理；正式入口用 bindings 路由 |

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

正式版本历史：`/configs/bindings/<pk>/versions/`；旧路由与 `ConfigVersion` 延后删除。

---

## 维护规则

1. 新增缺口续编 **Q96+**，同步改 AGENTS 与本文件。  
2. 需求正文（02–12）描述**已实现**行为；关闭项记入上表即可。
