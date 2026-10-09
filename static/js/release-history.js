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
        items: {},
        versionDialog: null,
        taskId: null,
        afterLogId: 0,
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

    function toast(message, type) {
        if (window.showToast) {
            window.showToast(message, type || "info");
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
            rows = rows.filter(function (item) { return !item.disabled; });
            var checkedCount = rows.filter(function (item) { return item.checked; }).length;
            input.checked = rows.length > 0 && checkedCount === rows.length;
            input.indeterminate = checkedCount > 0 && checkedCount < rows.length;
            input.disabled = rows.length === 0;
        });
        var pageRows = currentPageItems().filter(function (item) { return !item.disabled; });
        var selectedRows = pageRows.filter(function (item) { return item.checked; }).length;
        var pageInput = document.getElementById("historySelectPage");
        if (pageInput) {
            pageInput.checked = pageRows.length > 0 && selectedRows === pageRows.length;
            pageInput.indeterminate = selectedRows > 0 && selectedRows < pageRows.length;
            pageInput.disabled = pageRows.length === 0;
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

    function renderBatch(batch, latestBindings) {
        var fragment = document.createDocumentFragment();
        var batchRow = node("tr", "table-light");
        batchRow.dataset.batchKey = batch.batch_number;
        var selectCell = node("td", "text-center");
        if (canPublish) {
            selectCell.appendChild(checkBox(
                "选择批次 " + batch.batch_number,
                batch.batch_number,
                "historyBatchSelect",
                false,
                false
            ));
        }
        batchRow.appendChild(selectCell);
        var summaryCell = node("td", "", "");
        summaryCell.colSpan = 6;
        var toggle = node("button", "btn btn-link btn-sm p-0 fw-semibold text-decoration-none");
        toggle.type = "button";
        toggle.dataset.historyBatchToggle = batch.batch_number;
        toggle.setAttribute("aria-expanded", "false");
        toggle.appendChild(node("i", "bi bi-chevron-right me-1"));
        toggle.appendChild(node("span", "", batch.batch_number));
        summaryCell.appendChild(toggle);
        summaryCell.appendChild(node("span", "ms-2", batch.operation_type === "release_rollback" ? "回滚批次" : "发布批次"));
        summaryCell.appendChild(badge(batch.status));
        summaryCell.appendChild(node("span", "small text-muted ms-2", batch.summary));
        summaryCell.appendChild(node("span", "small text-muted ms-2", batch.created_at || ""));
        summaryCell.appendChild(node("span", "small text-muted ms-2", batch.operator || "-"));
        batchRow.appendChild(summaryCell);
        fragment.appendChild(batchRow);

        (batch.nodes || []).forEach(function (serverNode) {
            var nodeKey = String(batch.batch_number) + ":" + String(serverNode.id);
            var nodeRow = node("tr", "release-history-node");
            nodeRow.dataset.batchKey = batch.batch_number;
            nodeRow.dataset.nodeKey = nodeKey;
            nodeRow.hidden = true;
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
            nodeCell.appendChild(node("strong", "", serverNode.hostname || "未知节点"));
            nodeCell.appendChild(node("span", "small text-muted ms-2", serverNode.ip || ""));
            nodeCell.appendChild(node("span", "small text-muted ms-2", (serverNode.bindings || []).length + " 项配置"));
            if (serverNode.is_deleted) {
                nodeCell.appendChild(node("span", "badge bg-secondary ms-2", "节点已删除"));
            }
            nodeCell.appendChild(badge(serverNode.status));
            nodeRow.appendChild(nodeCell);
            fragment.appendChild(nodeRow);

            (serverNode.bindings || []).forEach(function (item) {
                var key = itemKey(item);
                var alreadyShown = latestBindings.has(String(item.binding_id));
                var isLatest = !alreadyShown;
                latestBindings.add(String(item.binding_id));
                var row = node("tr", "release-history-item");
                row.dataset.batchKey = batch.batch_number;
                row.dataset.nodeKey = nodeKey;
                row.dataset.itemKey = key;
                row.hidden = true;
                var checkboxCell = node("td", "text-center");
                if (canPublish) {
                    var reason = alreadyShown ? "该绑定已有更新的发布历史" : item.rollback_reason;
                    var canSelect = item.can_batch_rollback && !alreadyShown;
                    checkboxCell.appendChild(checkBox(
                        "选择 " + item.config_name + " 回滚至上一版",
                        String(item.binding_id),
                        "historyItemSelect",
                        !canSelect,
                        Boolean(state.selection[String(item.binding_id)])
                    ));
                    if (!canSelect && reason) {
                        checkboxCell.firstChild.title = reason;
                    }
                }
                row.appendChild(checkboxCell);

                var configCell = node("td", "");
                configCell.appendChild(node("div", "fw-semibold", item.config_name || "未知配置"));
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
        var latestBindings = new Set();
        if (!response.items.length) {
            var emptyRow = node("tr");
            var emptyCell = node("td", "text-center text-muted py-4", "没有符合条件的发布历史");
            emptyCell.colSpan = 7;
            emptyRow.appendChild(emptyCell);
            tbody.appendChild(emptyRow);
        } else {
            response.items.forEach(function (batch) {
                tbody.appendChild(renderBatch(batch, latestBindings));
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

    function previewHistoryVersion(item, version) {
        if (!item || !version) {
            return;
        }
        $("#historyPreviewTitle").text((item.config_name || "配置") + " · V" + version + " 预览");
        $("#historyPreviewContent").text("正在加载…");
        bootstrap.Modal.getOrCreateInstance(document.getElementById("historyPreviewModal")).show();
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
                row.appendChild(node("td", "fw-semibold", "V" + version.version));
                row.appendChild(node("td", "small text-break", version.remark || "-"));
                row.appendChild(node("td", "small", version.created_by || "-"));
                row.appendChild(node("td", "small text-nowrap", (version.created_at || "").replace("T", " ").slice(0, 16)));
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

    function renderTaskTree(tree) {
        var container = document.getElementById("historyProgressTree");
        container.textContent = "";
        if (!tree || !tree.nodes || !tree.nodes.length) {
            container.appendChild(node("span", "small text-muted", "等待任务进度…"));
            return;
        }
        if (tree.summary) {
            container.appendChild(node(
                "div",
                "small text-muted border-bottom pb-2 mb-2",
                "成功 " + tree.summary.success + "，失败 " + tree.summary.failed + "，共 " + tree.summary.total
            ));
        }
        tree.nodes.forEach(function (taskNode) {
            var section = node("div", "border-bottom py-2");
            section.appendChild(node("div", "small fw-semibold mb-1", taskNode.hostname + " (" + taskNode.ip + ") · " + statusLabel(taskNode.status)));
            (taskNode.bindings || []).forEach(function (item) {
                section.appendChild(node(
                    "div",
                    "small text-break",
                    item.config_name + " V" + item.version + " · " + statusLabel(item.status) + " · " + item.message
                ));
            });
            container.appendChild(section);
        });
    }

    function appendLogs(logs) {
        var logElement = document.getElementById("historyProgressLogs");
        (logs || []).forEach(function (entry) {
            logElement.appendChild(document.createTextNode((entry.created_at || "") + "  " + (entry.message || "") + "\n"));
        });
        logElement.scrollTop = logElement.scrollHeight;
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
        $.getJSON("/api/tasks/" + encodeURIComponent(state.taskId), {
            after_log_id: state.afterLogId,
            log_limit: 300
        }).done(function (task) {
            $("#historyProgressDetail").text(task.detail || "回滚任务执行中");
            $("#historyProgressPercent").text(task.progress + "%");
            $("#historyProgressBar").css("width", task.progress + "%").attr("aria-valuenow", task.progress);
            if (task.status !== "pending" && task.status !== "running") {
                $("#historyProgressBar").removeClass("progress-bar-striped progress-bar-animated");
            }
            renderTaskTree(task.result_tree);
            appendLogs(task.logs || []);
            state.afterLogId = task.next_log_id || state.afterLogId;
            if (["success", "failed", "cancelled"].indexOf(task.status) >= 0) {
                $("#historyProgressTitle").text(task.status === "success" ? "回滚完成" : (task.status === "cancelled" ? "回滚已取消" : "回滚失败"));
                $("#historyProgressFinal").text(task.detail || "").toggleClass("text-danger", task.status !== "success").toggleClass("text-success", task.status === "success");
                state.taskId = null;
                loadHistory(state.page);
                return;
            }
            schedulePoll();
        }).fail(function () {
            $("#historyProgressDetail").text("任务状态暂时无法读取，正在重试");
            schedulePoll();
        });
    }

    function openProgress(taskId, batchNumber) {
        if (state.pollTimer) {
            window.clearTimeout(state.pollTimer);
        }
        state.taskId = taskId;
        state.afterLogId = 0;
        $("#historyProgressTitle").text("回滚进度 · " + batchNumber);
        $("#historyProgressDetail").text("任务已创建");
        $("#historyProgressPercent").text("0%");
        $("#historyProgressBar").css("width", "0%").addClass("progress-bar-striped progress-bar-animated").removeClass("bg-success bg-danger");
        $("#historyProgressTree, #historyProgressLogs, #historyProgressFinal").empty().removeClass("text-danger text-success");
        bootstrap.Modal.getOrCreateInstance(document.getElementById("historyProgressModal")).show();
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
            toast(message, "success");
            openProgress(response.task_id, response.batch_number);
        }).fail(function (xhr) {
            var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "回滚任务创建失败";
            toast(message, "error");
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
        $("#historyRows").on("click", "[data-history-batch-toggle]", function () {
            var batchKey = this.dataset.historyBatchToggle;
            var expanded = this.getAttribute("aria-expanded") === "true";
            this.setAttribute("aria-expanded", expanded ? "false" : "true");
            this.querySelector("i").className = expanded
                ? "bi bi-chevron-right me-1"
                : "bi bi-chevron-down me-1";
            Array.prototype.slice.call(document.querySelectorAll("#historyRows tr[data-batch-key]")).forEach(function (row) {
                if (row.dataset.batchKey === batchKey && row !== this.closest("tr")) {
                    row.hidden = expanded;
                }
            }, this);
            syncSelectionUi();
        });
        $("#historyRows").on("change", "[data-history-item-select]", function () {
            var bindingId = this.dataset.historyItemSelect;
            if (this.checked) {
                var selected = currentPageItems().find(function (input) {
                    return input.dataset.historyItemSelect === bindingId && !input.disabled;
                });
                var item = selected ? state.items[selected.closest("tr").dataset.itemKey] : null;
                if (!item) {
                    var matchingKey = Object.keys(state.items).find(function (key) {
                        return String(state.items[key].binding_id) === bindingId;
                    });
                    item = matchingKey ? state.items[matchingKey] : null;
                }
                if (item) {
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
            syncSelectionUi();
        });
        $("#historyRows").on("change", "[data-history-node-select]", function () {
            var row = this.closest("tr");
            var key = this.dataset.historyNodeSelect;
            currentPageItems().forEach(function (item) {
                var itemRow = item.closest("tr");
                if (itemRow.dataset.nodeKey === key && itemRow.dataset.batchKey === row.dataset.batchKey && !item.disabled) {
                    item.checked = this.checked;
                    item.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }, this);
        });
        $("#historyRows").on("change", "[data-history-batch-select]", function () {
            var batchKey = this.dataset.historyBatchSelect;
            currentPageItems().forEach(function (item) {
                var itemRow = item.closest("tr");
                if (itemRow.dataset.batchKey === batchKey && !item.disabled) {
                    item.checked = this.checked;
                    item.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }, this);
        });
        $("#historySelectPage").on("change", function () {
            currentPageItems().forEach(function (item) {
                if (!item.disabled) {
                    item.checked = this.checked;
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
        $("#historyVersionsRows").on("click", "[data-rollback-preview-version]", function () {
            if (state.versionDialog) {
                previewHistoryVersion(state.versionDialog.item, Number(this.dataset.rollbackPreviewVersion));
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
