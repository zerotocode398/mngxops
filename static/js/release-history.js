(function ($, window, document) {
    "use strict";

    var root = document.getElementById("releaseHistory");
    if (!root) {
        return;
    }

    var canPublish = root.getAttribute("data-can-publish") === "true";
    var state = {
        page: 1,
        pageSize: 10,
        totalPages: 1,
        selection: {},
        selectionBatchKey: null,
        items: {},
        expandedBatches: {},
        expandedNodes: {},
        versionDialog: null,
        previewReturnToVersions: false,
        taskId: null,
        pollTimer: null,
        pendingRollback: null
    };

    function node(tagName, className, text) {
        var element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        if (text !== undefined && text !== null) {
            element.textContent = String(text);
        }
        return element;
    }

    function toast(message, type, duration, action) {
        if (window.showToast) {
            window.showToast(message, type || "info", duration, action);
        }
    }

    function statusLabel(status) {
        var labels = {
            pending: "等待执行",
            running: "执行中",
            waiting_reload: "等待 reload",
            success: "成功",
            failed: "失败",
            partial: "部分失败",
            cancelled: "已取消"
        };
        return labels[status] || status || "未知";
    }

    function statusClass(status) {
        if (status === "success") {
            return "bg-success";
        }
        if (status === "failed" || status === "partial") {
            return "bg-danger";
        }
        if (status === "running" || status === "pending" || status === "waiting_reload") {
            return "bg-primary";
        }
        return "bg-secondary";
    }

    function badge(status) {
        return node("span", "badge " + statusClass(status), statusLabel(status));
    }

    function checkBox(label, key, selector, disabled, checked) {
        var input = node("input", "form-check-input release-history-check");
        input.type = "checkbox";
        input.setAttribute("aria-label", label);
        input.dataset[selector] = key;
        input.disabled = Boolean(disabled);
        input.checked = Boolean(checked);
        return input;
    }

    function currentPageItems() {
        return Array.prototype.slice.call(
            document.querySelectorAll("[data-history-item-select]")
        );
    }

    function syncSelectionUi() {
        var count = Object.keys(state.selection).length;
        $("#historySelectedCount").text(count);
        $("#historyRollbackSelected").prop("disabled", !canPublish || count === 0);
        currentPageItems().forEach(function (input) {
            var selected = state.selection[input.dataset.historyItemSelect];
            var row = input.closest("tr");
            var item = row ? state.items[row.dataset.itemKey] : null;
            input.checked = Boolean(selected && item && selected.task_id === item.task_id);
        });
        document.querySelectorAll("[data-history-node-select], [data-history-batch-select]").forEach(function (input) {
            var selector = input.hasAttribute("data-history-node-select")
                ? "[data-history-item-select]"
                : "[data-history-item-select]";
            var key = input.dataset.historyNodeSelect || input.dataset.historyBatchSelect;
            var parentRow = input.closest("tr");
            var rows = Array.prototype.slice.call(document.querySelectorAll(selector)).filter(function (item) {
                var itemRow = item.closest("tr");
                if (!parentRow || !itemRow) {
                    return false;
                }
                if (input.hasAttribute("data-history-node-select")) {
                    return itemRow.dataset.nodeKey === key && itemRow.dataset.batchKey === parentRow.dataset.batchKey;
                }
                return itemRow.dataset.batchKey === key;
            });
            var selectableRows = rows.filter(function (item) { return !item.disabled; });
            var checkedCount = selectableRows.filter(function (item) { return item.checked; }).length;
            input.checked = selectableRows.length > 0 && checkedCount === selectableRows.length;
            input.indeterminate = checkedCount > 0 && checkedCount < selectableRows.length;
            input.disabled = selectableRows.length === 0;
            var selectionScope = input.hasAttribute("data-history-node-select") ? "该节点" : "该批次";
            if (input.disabled) {
                input.title = rows.map(function (item) { return item.title; }).filter(Boolean)[0]
                    || selectionScope + "没有可批量回滚的配置";
            } else if (input.hasAttribute("data-history-node-select")) {
                input.title = "选择该节点中所有可批量回滚的配置";
            } else {
                input.title = "选择该批次中所有可批量回滚的配置";
            }
            var parentCell = input.closest("td");
            if (parentCell) {
                parentCell.title = input.disabled ? input.title : "";
            }
        });
    }

    // 切换来源批次时清除旧选择并接管当前批次。
    function claimSelectionBatch(batchKey) {
        if (state.selectionBatchKey && state.selectionBatchKey !== batchKey) {
            state.selection = {};
        }
        state.selectionBatchKey = batchKey;
    }

    // 清空所选配置后释放当前批次限制。
    function resetSelectionBatchIfEmpty() {
        if (Object.keys(state.selection).length === 0) {
            state.selectionBatchKey = null;
        }
    }

    function queryParams(page) {
        var params = {
            search: window.getQueryTagValue("#historySearch").trim(),
            page: page,
            page_size: state.pageSize
        };
        var status = $("#historyStatus").val();
        if (status) {
            params.status = status;
        }
        return params;
    }

    // 汇总批次、节点与配置项的批量回滚资格和可见原因。
    function selectionEligibility(batch) {
        var items = {};
        var nodes = {};
        var batchCanSelect = false;
        var batchHasDisabledItems = false;
        (batch.nodes || []).forEach(function (serverNode) {
            var nodeKey = String(batch.batch_number) + ":" + String(serverNode.id);
            var nodeCanSelect = false;
            var nodeHasDisabledItems = false;
            (serverNode.bindings || []).forEach(function (item) {
                var key = itemKey(item);
                var canSelect = Boolean(item.can_batch_rollback);
                var reason = item.batch_rollback_reason || (
                    !item.previous_version
                        ? "没有其他可用配置版本"
                        : "该配置当前不满足批量回滚条件"
                );
                items[key] = {can_select: canSelect, reason: canSelect ? "" : reason};
                nodeCanSelect = nodeCanSelect || canSelect;
                nodeHasDisabledItems = nodeHasDisabledItems || !canSelect;
            });
            nodes[nodeKey] = {
                can_select: nodeCanSelect,
                has_disabled_items: nodeHasDisabledItems
            };
            batchCanSelect = batchCanSelect || nodeCanSelect;
            batchHasDisabledItems = batchHasDisabledItems || nodeHasDisabledItems;
        });
        return {
            items: items,
            nodes: nodes,
            batch_can_select: batchCanSelect,
            batch_has_disabled_items: batchHasDisabledItems
        };
    }

    function addPreviewButton(parent, item, version, title) {
        var button = node("button", "btn btn-link btn-sm p-0 text-decoration-none", title);
        button.type = "button";
        button.dataset.historyPreviewKey = itemKey(item);
        button.dataset.versionNumber = version || "";
        button.disabled = !version;
        button.title = version ? "预览配置正文" : "历史版本快照不可用";
        parent.appendChild(button);
    }

    function itemKey(item) {
        return String(item.task_id) + ":" + String(item.binding_id);
    }

    function renderBatch(batch) {
        var fragment = document.createDocumentFragment();
        var eligibility = selectionEligibility(batch);
        var batchRow = node("tr", "release-history-batch");
        batchRow.dataset.batchKey = batch.batch_number;
        var batchExpanded = Boolean(state.expandedBatches[batch.batch_number]);
        var selectCell = node("td", "text-center");
        if (canPublish) {
            selectCell.appendChild(checkBox(
                "选择批次 " + batch.batch_number,
                batch.batch_number,
                "historyBatchSelect",
                !eligibility.batch_can_select,
                false
            ));
        }
        batchRow.appendChild(selectCell);
        var summaryCell = node("td", "", "");
        summaryCell.colSpan = 6;
        var toggle = node("button", "btn btn-link btn-sm p-0 me-1 release-history-tree-toggle");
        toggle.type = "button";
        toggle.dataset.historyBatchToggle = batch.batch_number;
        toggle.setAttribute("aria-expanded", batchExpanded ? "true" : "false");
        toggle.setAttribute("aria-label", (batchExpanded ? "收起" : "展开") + "批次明细 " + batch.batch_number);
        toggle.title = batchExpanded ? "收起批次明细" : "展开批次明细";
        toggle.appendChild(node("i", batchExpanded ? "bi bi-chevron-down" : "bi bi-chevron-right"));
        var summary = node("div", "release-history-batch-summary");
        summary.appendChild(toggle);
        var batchLink = node("a", "release-history-batch-link");
        batchLink.href = "/tasks/" + encodeURIComponent(batch.task_id) + "/";
        batchLink.target = "_blank";
        batchLink.rel = "noopener noreferrer";
        batchLink.title = "查看对应任务详情";
        batchLink.appendChild(node("code", "release-history-batch-code", batch.batch_number));
        summary.appendChild(batchLink);
        summary.appendChild(node("span", "release-history-batch-meta", batch.operation_type === "release_rollback" ? "回滚批次" : "发布批次"));
        summary.appendChild(badge(batch.status));
        summary.appendChild(node("span", "release-history-batch-meta", batch.summary));
        var batchEligibilityLabel = !eligibility.batch_can_select
            ? "无可批量回滚配置"
            : (eligibility.batch_has_disabled_items ? "部分配置不可批量回滚" : "");
        if (canPublish && batchEligibilityLabel) {
            summary.appendChild(node(
                "span",
                "badge release-history-eligibility",
                batchEligibilityLabel
            ));
        }
        summary.appendChild(node("span", "release-history-batch-meta", window.formatUtc8DateTime(batch.created_at)));
        summary.appendChild(node("span", "release-history-batch-meta", batch.operator || "-"));
        summaryCell.appendChild(summary);
        batchRow.appendChild(summaryCell);
        fragment.appendChild(batchRow);

        (batch.nodes || []).forEach(function (serverNode) {
            var nodeKey = String(batch.batch_number) + ":" + String(serverNode.id);
            var nodeRow = node("tr", "release-history-node");
            nodeRow.dataset.batchKey = batch.batch_number;
            nodeRow.dataset.nodeKey = nodeKey;
            nodeRow.hidden = !batchExpanded;
            var nodeSelectCell = node("td", "text-center");
            if (canPublish) {
                nodeSelectCell.appendChild(checkBox(
                    "选择节点 " + serverNode.hostname,
                    nodeKey,
                    "historyNodeSelect",
                    false,
                    false
                ));
            }
            nodeRow.appendChild(nodeSelectCell);
            var nodeCell = node("td", "", "");
            nodeCell.colSpan = 6;
            var nodeExpanded = Boolean(state.expandedNodes[nodeKey]);
            var nodeToggle = node("button", "btn btn-link btn-sm p-0 me-1 release-history-tree-toggle");
            nodeToggle.type = "button";
            nodeToggle.dataset.historyNodeToggle = nodeKey;
            nodeToggle.setAttribute("aria-expanded", nodeExpanded ? "true" : "false");
            nodeToggle.setAttribute("aria-label", (nodeExpanded ? "收起" : "展开") + "节点配置 " + serverNode.hostname);
            nodeToggle.title = nodeExpanded ? "收起节点配置" : "展开节点配置";
            nodeToggle.appendChild(node("i", nodeExpanded ? "bi bi-chevron-down me-1" : "bi bi-chevron-right me-1"));
            nodeCell.appendChild(nodeToggle);
            var nodeIcon = node("i", "bi bi-hdd-network me-1 text-secondary");
            nodeIcon.setAttribute("aria-hidden", "true");
            nodeCell.appendChild(nodeIcon);
            nodeCell.appendChild(node("span", "release-history-level-label", "节点"));
            nodeCell.appendChild(node("strong", "", serverNode.hostname || "未知节点"));
            nodeCell.appendChild(node("span", "small text-muted ms-2", serverNode.ip || ""));
            nodeCell.appendChild(node("span", "small text-muted ms-2", (serverNode.bindings || []).length + " 项配置"));
            if (serverNode.is_deleted) {
                nodeCell.appendChild(node("span", "badge bg-secondary ms-2", "节点已删除"));
            }
            nodeCell.appendChild(badge(serverNode.status));
            var nodeEligibility = eligibility.nodes[nodeKey];
            var nodeEligibilityLabel = !nodeEligibility.can_select
                ? "无可批量回滚配置"
                : (nodeEligibility.has_disabled_items ? "部分配置不可批量回滚" : "");
            if (canPublish && nodeEligibilityLabel) {
                nodeCell.appendChild(node(
                    "span",
                    "badge release-history-eligibility ms-2",
                    nodeEligibilityLabel
                ));
            }
            nodeRow.appendChild(nodeCell);
            fragment.appendChild(nodeRow);

            (serverNode.bindings || []).forEach(function (item) {
                var key = itemKey(item);
                var itemEligibility = eligibility.items[key];
                var row = node("tr", "release-history-item");
                row.dataset.batchKey = batch.batch_number;
                row.dataset.nodeKey = nodeKey;
                row.dataset.itemKey = key;
                row.hidden = !batchExpanded || !nodeExpanded;
                var checkboxCell = node("td", "text-center");
                var disabledReason = itemEligibility.reason;
                if (canPublish) {
                    var canSelect = itemEligibility.can_select;
                    checkboxCell.appendChild(checkBox(
                        "选择 " + item.config_name + " 回滚至上一版",
                        String(item.binding_id),
                        "historyItemSelect",
                        !canSelect,
                        Boolean(
                            state.selection[String(item.binding_id)]
                            && state.selection[String(item.binding_id)].task_id === item.task_id
                        )
                    ));
                    if (!canSelect && disabledReason) {
                        checkboxCell.firstChild.title = disabledReason;
                    }
                }
                row.appendChild(checkboxCell);

                var configCell = node("td", "");
                var configName = node("div", "fw-semibold release-history-config-name");
                var configIcon = node("i", "bi bi-file-earmark-code me-1 text-primary");
                configIcon.setAttribute("aria-hidden", "true");
                configName.appendChild(configIcon);
                configName.appendChild(node("span", "release-history-level-label", "配置"));
                configName.appendChild(document.createTextNode(item.config_name || "未知配置"));
                configCell.appendChild(configName);
                if (canPublish && disabledReason) {
                    configCell.appendChild(node(
                        "div",
                        "release-history-disabled-reason",
                        "不可批量回滚：" + disabledReason
                    ));
                }
                configCell.appendChild(node("code", "small text-muted release-history-path", item.remote_path || "-"));
                row.appendChild(configCell);
                row.appendChild(node("td", "small", (item.hostname || "-") + (item.ip ? " (" + item.ip + ")" : "")));

                var versionCell = node("td", "release-history-version");
                if (item.version !== null && item.version !== undefined) {
                    addPreviewButton(versionCell, item, item.version, "V" + item.version);
                } else {
                    versionCell.appendChild(node("span", "text-muted", "-"));
                }
                row.appendChild(versionCell);
                var stateCell = node("td", "");
                stateCell.appendChild(badge(item.status));
                if (item.action === "delete") {
                    stateCell.appendChild(node("span", "badge bg-secondary ms-1", "远程删除"));
                }
                row.appendChild(stateCell);
                row.appendChild(node("td", "small text-break", item.message || "-"));

                var actionCell = node("td", "text-end");
                actionCell.className = "text-end";
                if (canPublish) {
                    var rollbackButton = node("button", "btn btn-outline-danger btn-sm", "");
                    rollbackButton.type = "button";
                    rollbackButton.dataset.singleRollback = key;
                    rollbackButton.title = item.previous_version ? "回滚至 V" + item.previous_version : (item.rollback_reason || "没有上一版");
                    rollbackButton.setAttribute("aria-label", "回滚至上一版");
                    rollbackButton.appendChild(node("i", "bi bi-arrow-counterclockwise", ""));
                    rollbackButton.disabled = !item.can_rollback || !item.previous_version;
                    actionCell.appendChild(rollbackButton);
                    var chooseVersionButton = node("button", "btn btn-outline-secondary btn-sm ms-1", "");
                    chooseVersionButton.type = "button";
                    chooseVersionButton.dataset.chooseRollbackVersion = key;
                    chooseVersionButton.title = "选择其他回滚版本";
                    chooseVersionButton.setAttribute("aria-label", "选择其他回滚版本");
                    chooseVersionButton.appendChild(node("i", "bi bi-list-ol", ""));
                    chooseVersionButton.disabled = !item.can_rollback;
                    actionCell.appendChild(chooseVersionButton);
                } else if (!item.can_rollback) {
                    actionCell.appendChild(node("span", "small text-muted", item.rollback_reason || "不可回滚"));
                }
                row.appendChild(actionCell);
                state.items[key] = item;
                fragment.appendChild(row);
            });
        });
        return fragment;
    }

    function renderHistory(response) {
        var tbody = document.getElementById("historyRows");
        tbody.textContent = "";
        state.items = {};
        if (!response.items.length) {
            var emptyRow = node("tr");
            var emptyCell = node("td", "text-center text-muted py-4", "没有符合条件的发布历史");
            emptyCell.colSpan = 7;
            emptyRow.appendChild(emptyCell);
            tbody.appendChild(emptyRow);
        } else {
            response.items.forEach(function (batch) {
                tbody.appendChild(renderBatch(batch));
            });
        }
        state.page = response.page;
        state.totalPages = response.total_pages;
        $("#historyPaginationSummary").text("共 " + response.total + " 个批次 · 第 " + response.page + " / " + response.total_pages + " 页 · 每页 " + state.pageSize + " 个");
        $("#historyPreviousPage").prop("disabled", state.page <= 1);
        $("#historyNextPage").prop("disabled", state.page >= state.totalPages);
        syncSelectionUi();
    }

    function loadHistory(page) {
        state.selection = {};
        state.selectionBatchKey = null;
        syncSelectionUi();
        var loadingRow = node("tr");
        var loadingCell = node("td", "text-center text-muted py-4", "正在加载发布历史…");
        loadingCell.colSpan = 7;
        loadingRow.appendChild(loadingCell);
        $("#historyRows").empty().append(loadingRow);
        $.getJSON("/api/releases/history", queryParams(page))
            .done(renderHistory)
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "发布历史读取失败";
                $("#historyRows").empty();
                var row = node("tr");
                var cell = node("td", "text-center text-danger py-4", message);
                cell.colSpan = 7;
                row.appendChild(cell);
                document.getElementById("historyRows").appendChild(row);
            });
    }

    function selectionFromPage() {
        return Object.keys(state.selection).map(function (bindingId) {
            return state.selection[bindingId];
        });
    }

    // 同步更新批次行及其节点、配置子行的可见状态。
    function setBatchExpanded(batchKey, expanded) {
        state.expandedBatches[batchKey] = expanded;
        Array.prototype.slice.call(document.querySelectorAll("#historyRows tr[data-batch-key]"))
            .filter(function (row) { return row.dataset.batchKey === batchKey; })
            .forEach(function (row) {
                if (row.classList.contains("release-history-batch")) {
                    var toggle = row.querySelector("[data-history-batch-toggle]");
                    if (toggle) {
                        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
                        toggle.setAttribute("aria-label", (expanded ? "收起" : "展开") + "批次明细 " + batchKey);
                        toggle.title = expanded ? "收起批次明细" : "展开批次明细";
                        toggle.querySelector("i").className = expanded ? "bi bi-chevron-down" : "bi bi-chevron-right";
                    }
                    return;
                }
                if (row.classList.contains("release-history-node")) {
                    row.hidden = !expanded;
                    return;
                }
                row.hidden = !expanded || !state.expandedNodes[row.dataset.nodeKey];
            });
        syncSelectionUi();
    }

    // 切换指定批次的展开状态。
    function toggleBatch(batchKey) {
        setBatchExpanded(batchKey, !state.expandedBatches[batchKey]);
    }

    // 切换节点配置明细的展开状态。
    function toggleNode(nodeKey) {
        var expanded = !state.expandedNodes[nodeKey];
        state.expandedNodes[nodeKey] = expanded;
        var nodeRow = Array.prototype.slice.call(
            document.querySelectorAll("#historyRows tr[data-node-key]")
        ).find(function (row) { return row.dataset.nodeKey === nodeKey; });
        if (!nodeRow) {
            return;
        }
        var hostname = nodeRow.querySelector("strong").textContent;
        var toggle = nodeRow.querySelector("[data-history-node-toggle]");
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        toggle.setAttribute("aria-label", (expanded ? "收起" : "展开") + "节点配置 " + hostname);
        toggle.title = expanded ? "收起节点配置" : "展开节点配置";
        toggle.querySelector("i").className = expanded ? "bi bi-chevron-down me-1" : "bi bi-chevron-right me-1";
        Array.prototype.slice.call(document.querySelectorAll("#historyRows tr[data-item-key]"))
            .filter(function (row) { return row.dataset.nodeKey === nodeKey; })
            .forEach(function (row) {
                row.hidden = !state.expandedBatches[nodeRow.dataset.batchKey] || !expanded;
            });
    }

    function showConfirm(items) {
        if (!items.length) {
            return;
        }
        state.pendingRollback = items;
        var body = document.getElementById("historyConfirmRows");
        body.textContent = "";
        items.forEach(function (item) {
            var row = node("tr");
            row.appendChild(node("td", "", (item.hostname || "-") + (item.ip ? " (" + item.ip + ")" : "")));
            row.appendChild(node("td", "", item.config_name || "-"));
            row.appendChild(node("td", "", item.version ? "V" + item.version : "-"));
            row.appendChild(node("td", "fw-semibold", item.target_version ? "V" + item.target_version : "-"));
            body.appendChild(row);
        });
        $("#historyConfirmSummary").text(
            items.length === 1
                ? "将为所选配置创建新的异步回滚任务。执行前会备份远程文件，并在全部配置通过 nginx -t 后统一 reload。"
                : "将回滚 " + items.length + " 项配置；批量操作按各自历史版本的上一版执行。远程文件会先备份，节点内统一 reload。"
        );
        bootstrap.Modal.getOrCreateInstance(document.getElementById("historyConfirmModal")).show();
    }

    function historyVersionUrl(item, version) {
        return "/api/releases/history/" + encodeURIComponent(item.task_id)
            + "/bindings/" + encodeURIComponent(item.binding_id)
            + "/versions/" + encodeURIComponent(version);
    }

    function previewHistoryVersion(item, version, returnToVersions) {
        if (!item || !version) {
            return;
        }
        $("#historyPreviewTitle").text((item.config_name || "配置") + " · V" + version + " 预览");
        $("#historyPreviewContent").text("正在加载…");
        var previewModal = document.getElementById("historyPreviewModal");
        var versionModal = document.getElementById("historyVersionsModal");
        state.previewReturnToVersions = Boolean(returnToVersions);
        if (returnToVersions && versionModal.classList.contains("show")) {
            versionModal.addEventListener("hidden.bs.modal", function showPreview() {
                bootstrap.Modal.getOrCreateInstance(previewModal).show();
            }, {once: true});
            bootstrap.Modal.getOrCreateInstance(versionModal).hide();
        } else {
            bootstrap.Modal.getOrCreateInstance(previewModal).show();
        }
        $.getJSON(historyVersionUrl(item, version))
            .done(function (response) {
                $("#historyPreviewContent").text(response.content || "");
            })
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "版本内容读取失败";
                $("#historyPreviewContent").text(message);
            });
    }

    function renderRollbackVersions(response) {
        var dialog = state.versionDialog;
        dialog.page = response.page;
        dialog.totalPages = response.total_pages;
        var tbody = document.getElementById("historyVersionsRows");
        tbody.textContent = "";
        if (!response.versions.length) {
            var emptyRow = node("tr");
            var emptyCell = node("td", "text-center text-muted py-3", "没有其他可回滚版本");
            emptyCell.colSpan = 6;
            emptyRow.appendChild(emptyCell);
            tbody.appendChild(emptyRow);
        } else {
            if (dialog.selectedVersion === null) {
                var previous = response.versions.find(function (version) {
                    return version.version === dialog.item.previous_version;
                });
                dialog.selectedVersion = previous
                    ? previous.version
                    : response.versions[0].version;
            }
            response.versions.forEach(function (version) {
                var row = node("tr");
                var radioCell = node("td", "text-center");
                var radio = node("input", "form-check-input release-history-check");
                radio.type = "radio";
                radio.name = "historyRollbackVersion";
                radio.value = String(version.version);
                radio.dataset.rollbackVersionChoice = String(version.version);
                radio.checked = dialog.selectedVersion === version.version;
                radio.setAttribute("aria-label", "选择回滚至 V" + version.version);
                radioCell.appendChild(radio);
                row.appendChild(radioCell);
                row.dataset.rollbackVersionRow = String(version.version);
                row.appendChild(node("td", "fw-semibold", "V" + version.version));
                row.appendChild(node("td", "small text-break", version.remark || "-"));
                row.appendChild(node("td", "small", version.created_by || "-"));
                row.appendChild(node("td", "small text-nowrap", window.formatUtc8DateTime(version.created_at).slice(0, 16)));
                var previewCell = node("td", "text-end");
                var previewButton = node("button", "btn btn-outline-secondary btn-sm", "");
                previewButton.type = "button";
                previewButton.dataset.rollbackPreviewVersion = String(version.version);
                previewButton.title = "预览版本正文";
                previewButton.setAttribute("aria-label", "预览版本正文");
                previewButton.appendChild(node("i", "bi bi-eye", ""));
                previewCell.appendChild(previewButton);
                row.appendChild(previewCell);
                tbody.appendChild(row);
            });
        }
        $("#historyVersionsSummary").text(
            dialog.item.config_name + " · 已发布 V" + dialog.item.version
            + (dialog.selectedVersion ? " · 回滚至 V" + dialog.selectedVersion : "")
        );
        $("#historyVersionsPaginationSummary").text(
            "共 " + response.total + " 个版本 · 第 " + response.page + " / " + response.total_pages + " 页 · 每页 " + response.page_size + " 个"
        );
        $("#historyVersionsPreviousPage").prop("disabled", response.page <= 1);
        $("#historyVersionsNextPage").prop("disabled", response.page >= response.total_pages);
        $("#historyChooseVersionSubmit").prop("disabled", dialog.selectedVersion === null);
    }

    function loadRollbackVersions(page) {
        var dialog = state.versionDialog;
        if (!dialog) {
            return;
        }
        var url = "/api/releases/history/" + encodeURIComponent(dialog.item.task_id)
            + "/bindings/" + encodeURIComponent(dialog.item.binding_id) + "/versions";
        $("#historyVersionsRows").empty();
        var loadingRow = node("tr");
        var loadingCell = node("td", "text-center text-muted py-3", "正在加载版本…");
        loadingCell.colSpan = 6;
        loadingRow.appendChild(loadingCell);
        document.getElementById("historyVersionsRows").appendChild(loadingRow);
        $.getJSON(url, {page: page, page_size: 15})
            .done(renderRollbackVersions)
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "版本列表读取失败";
                var errorRow = node("tr");
                var errorCell = node("td", "text-center text-danger py-3", message);
                errorCell.colSpan = 6;
                errorRow.appendChild(errorCell);
                document.getElementById("historyVersionsRows").textContent = "";
                document.getElementById("historyVersionsRows").appendChild(errorRow);
            });
    }

    function openVersionChooser(item) {
        state.versionDialog = {
            item: item,
            page: 1,
            totalPages: 1,
            selectedVersion: item.previous_version
        };
        bootstrap.Modal.getOrCreateInstance(document.getElementById("historyVersionsModal")).show();
        loadRollbackVersions(1);
    }

    function showRollbackTaskToast(task, fallback) {
        var taskId = task && (task.id || task.task_id);
        var status = task && task.status;
        var type = status === "success"
            ? "success"
            : status === "failed" ? "danger" : status === "cancelled" ? "warning" : "info";
        var action = taskId ? {
            label: "查看完整日志",
            href: "/tasks/" + encodeURIComponent(taskId) + "/",
            target: "_blank"
        } : undefined;
        toast((task && task.detail) || fallback || "回滚任务已结束", type, 3000, action);
    }

    function schedulePoll() {
        if (state.taskId) {
            state.pollTimer = window.setTimeout(pollTask, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
        }
    }

    function pollTask() {
        if (!state.taskId) {
            return;
        }
        $.getJSON("/api/tasks/" + encodeURIComponent(state.taskId)).done(function (task) {
            if (["success", "failed", "cancelled"].indexOf(task.status) >= 0) {
                state.taskId = null;
                showRollbackTaskToast(task, "回滚任务已结束");
                loadHistory(state.page);
                return;
            }
            schedulePoll();
        }).fail(function () {
            schedulePoll();
        });
    }

    function trackRollbackTask(taskId, batchNumber, detail) {
        if (state.pollTimer) {
            window.clearTimeout(state.pollTimer);
        }
        state.taskId = taskId;
        showRollbackTaskToast({
            id: taskId,
            status: "pending",
            detail: detail || "回滚批次 " + batchNumber + " 已创建，正在执行"
        }, "回滚任务已创建");
        pollTask();
    }

    function submitRollback() {
        var items = state.pendingRollback || [];
        if (!items.length) {
            return;
        }
        $("#historyConfirmSubmit").prop("disabled", true);
        $.ajax({
            url: "/api/releases/rollback",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({
                items: items.map(function (item) {
                    var value = {task_id: item.task_id, binding_id: item.binding_id};
                    if (item.custom_version) {
                        value.version = item.target_version;
                    }
                    return value;
                })
            })
        }).done(function (response) {
            bootstrap.Modal.getOrCreateInstance(document.getElementById("historyConfirmModal")).hide();
            state.selection = {};
            syncSelectionUi();
            var message = response.message || "回滚任务已创建";
            if (response.skipped && response.skipped.length) {
                message += "；" + response.skipped.length + " 项因状态变化已跳过";
            }
            trackRollbackTask(response.task_id, response.batch_number, message);
        }).fail(function (xhr) {
            var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "回滚任务创建失败";
            toast(message, "danger");
        }).always(function () {
            $("#historyConfirmSubmit").prop("disabled", false);
        });
    }

    $(function () {
        $("#historyPageSize").val(String(state.pageSize));
        loadHistory(1);
        $("#historyFilterForm").on("submit", function (event) {
            event.preventDefault();
            loadHistory(1);
        });
        $("#historyStatus").on("change", function () {
            loadHistory(1);
        });
        $("#historyPageSize").on("change", function () {
            state.pageSize = Number(this.value) || 10;
            loadHistory(1);
        });
        $("#historyPreviousPage").on("click", function () {
            if (state.page > 1) { loadHistory(state.page - 1); }
        });
        $("#historyNextPage").on("click", function () {
            if (state.page < state.totalPages) { loadHistory(state.page + 1); }
        });
        $("#historyRows").on("click", "tr.release-history-batch", function (event) {
            if (event.target.closest("input, button, a:not(.release-history-batch-link)")) {
                return;
            }
            toggleBatch(this.dataset.batchKey);
        });
        $("#historyRows").on("click", "[data-history-batch-toggle]", function () {
            toggleBatch(this.dataset.historyBatchToggle);
        });
        $("#historyRows").on("click", "tr.release-history-node", function (event) {
            if (event.target.closest("input, button, a")) {
                return;
            }
            toggleNode(this.dataset.nodeKey);
        });
        $("#historyRows").on("click", "[data-history-node-toggle]", function (event) {
            event.stopPropagation();
            toggleNode(this.dataset.historyNodeToggle);
        });
        $("#historyRows").on("change", "[data-history-item-select]", function () {
            var bindingId = this.dataset.historyItemSelect;
            var row = this.closest("tr");
            if (this.checked) {
                var item = row ? state.items[row.dataset.itemKey] : null;
                if (item) {
                    claimSelectionBatch(row.dataset.batchKey);
                    state.selection[bindingId] = {
                        task_id: item.task_id,
                        binding_id: item.binding_id,
                        config_name: item.config_name,
                        hostname: item.hostname,
                        ip: item.ip,
                        version: item.version,
                        target_version: item.previous_version,
                        custom_version: false
                    };
                }
            } else {
                delete state.selection[bindingId];
            }
            resetSelectionBatchIfEmpty();
            syncSelectionUi();
        });
        $("#historyRows").on("change", "[data-history-node-select]", function () {
            var row = this.closest("tr");
            var key = this.dataset.historyNodeSelect;
            var checked = this.checked;
            if (checked) {
                claimSelectionBatch(row.dataset.batchKey);
            }
            currentPageItems().forEach(function (item) {
                var itemRow = item.closest("tr");
                if (itemRow.dataset.nodeKey === key && itemRow.dataset.batchKey === row.dataset.batchKey && !item.disabled) {
                    item.checked = checked;
                    item.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }, this);
        });
        $("#historyRows").on("change", "[data-history-batch-select]", function () {
            var batchKey = this.dataset.historyBatchSelect;
            var checked = this.checked;
            if (checked) {
                claimSelectionBatch(batchKey);
            }
            currentPageItems().forEach(function (item) {
                var itemRow = item.closest("tr");
                if (itemRow.dataset.batchKey === batchKey && !item.disabled) {
                    item.checked = checked;
                    item.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }, this);
        });
        $("#historyRows").on("click", "[data-history-preview-key]", function () {
            var item = state.items[this.dataset.historyPreviewKey];
            previewHistoryVersion(item, Number(this.dataset.versionNumber));
        });
        $("#historyRows").on("click", "[data-single-rollback]", function () {
            var key = this.dataset.singleRollback;
            var item = state.items[key];
            if (!item || !item.can_rollback || !item.previous_version) {
                return;
            }
            showConfirm([{
                task_id: item.task_id,
                binding_id: item.binding_id,
                config_name: item.config_name,
                hostname: item.hostname,
                ip: item.ip,
                version: item.version,
                target_version: item.previous_version,
                custom_version: true
            }]);
        });
        $("#historyRows").on("click", "[data-choose-rollback-version]", function () {
            var item = state.items[this.dataset.chooseRollbackVersion];
            if (item && item.can_rollback) {
                openVersionChooser(item);
            }
        });
        $("#historyVersionsRows").on("change", "[data-rollback-version-choice]", function () {
            if (state.versionDialog) {
                state.versionDialog.selectedVersion = Number(this.value);
                $("#historyVersionsSummary").text(
                    state.versionDialog.item.config_name + " · 已发布 V"
                    + state.versionDialog.item.version + " · 回滚至 V" + this.value
                );
                $("#historyChooseVersionSubmit").prop("disabled", false);
            }
        });
        $("#historyVersionsRows").on("click", "tr[data-rollback-version-row]", function (event) {
            if (event.target.closest("button, input, a")) return;
            var radio = this.querySelector("[data-rollback-version-choice]");
            if (radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event("change", {bubbles: true}));
            }
        });
        $("#historyVersionsRows").on("click", "[data-rollback-preview-version]", function () {
            if (state.versionDialog) {
                var version = Number(this.dataset.rollbackPreviewVersion);
                var radio = this.closest("tr").querySelector("[data-rollback-version-choice]");
                if (radio) {
                    radio.checked = true;
                    radio.dispatchEvent(new Event("change", {bubbles: true}));
                }
                previewHistoryVersion(state.versionDialog.item, version, true);
            }
        });
        document.getElementById("historyPreviewModal").addEventListener("hidden.bs.modal", function () {
            if (state.previewReturnToVersions && state.versionDialog) {
                state.previewReturnToVersions = false;
                bootstrap.Modal.getOrCreateInstance(document.getElementById("historyVersionsModal")).show();
            }
        });
        $("#historyVersionsPreviousPage").on("click", function () {
            if (state.versionDialog && state.versionDialog.page > 1) {
                loadRollbackVersions(state.versionDialog.page - 1);
            }
        });
        $("#historyVersionsNextPage").on("click", function () {
            if (state.versionDialog && state.versionDialog.page < state.versionDialog.totalPages) {
                loadRollbackVersions(state.versionDialog.page + 1);
            }
        });
        $("#historyChooseVersionSubmit").on("click", function () {
            var dialog = state.versionDialog;
            if (!dialog || dialog.selectedVersion === null) {
                return;
            }
            bootstrap.Modal.getOrCreateInstance(document.getElementById("historyVersionsModal")).hide();
            showConfirm([{
                task_id: dialog.item.task_id,
                binding_id: dialog.item.binding_id,
                config_name: dialog.item.config_name,
                hostname: dialog.item.hostname,
                ip: dialog.item.ip,
                version: dialog.item.version,
                target_version: dialog.selectedVersion,
                custom_version: true
            }]);
        });
        $("#historyRollbackSelected").on("click", function () {
            showConfirm(selectionFromPage());
        });
        $("#historyConfirmSubmit").on("click", submitRollback);
    });
}(jQuery, window, document));
