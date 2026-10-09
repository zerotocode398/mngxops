(function () {
    "use strict";

    var statusLabels = {
        pending: "等待中",
        running: "执行中",
        success: "成功",
        failed: "失败",
        cancelled: "已取消"
    };
    var fieldLabels = {};
    var logTargetMap = [];
    var terminalStatuses = ["success", "failed", "cancelled"];

    function statusBadge(status) {
        var badge = document.createElement("span");
        badge.className = "badge task-status-badge status-" + status;
        badge.setAttribute("data-task-status", "");
        badge.textContent = statusLabels[status] || status;
        return badge;
    }

    function updateProgress(root, progress) {
        var value = Math.max(0, Math.min(100, Number(progress) || 0));
        root.querySelectorAll("[data-task-progress-bar]").forEach(function (bar) {
            bar.style.width = value + "%";
        });
        root.querySelectorAll("[data-task-progress-text]").forEach(function (label) {
            label.textContent = value + "%";
        });
        root.querySelectorAll(".task-progress[role='progressbar'], .task-progress-track[role='progressbar']")
            .forEach(function (bar) { bar.setAttribute("aria-valuenow", String(value)); });
    }

    function updateStatus(root, status) {
        root.querySelectorAll("[data-task-status]").forEach(function (current) {
            current.replaceWith(statusBadge(status));
        });
    }

    function summaryLabel(value) {
        if (value && typeof value === "object") {
            return value.hostname || value.ip || value.name || value.path ||
                value.config_name || value.action || value.message || "结果项";
        }
        return String(value);
    }

    function containsFailure(value) {
        if (!value || typeof value !== "object") return false;
        if (value.status === "failed" || Number(value.failed) > 0) return true;
        return Object.keys(value).some(function (key) { return containsFailure(value[key]); });
    }

    function resultValue(value, key, expandAll) {
        if (Array.isArray(value)) {
            var details = document.createElement("details");
            details.open = expandAll || value.some(containsFailure);
            var summary = document.createElement("summary");
            summary.className = "task-tree-summary";
            summary.textContent = (fieldLabels[key] || key || "项目") + "（" + value.length + "）";
            details.appendChild(summary);
            var list = document.createElement("ul");
            list.className = "task-tree-list";
            value.forEach(function (item, index) {
                list.appendChild(resultItem(item, String(index + 1), expandAll));
            });
            details.appendChild(list);
            return details;
        }
        if (value && typeof value === "object") {
            var fields = document.createElement("dl");
            fields.className = "task-tree-fields";
            Object.keys(value).forEach(function (childKey) {
                var term = document.createElement("dt");
                term.textContent = fieldLabels[childKey] || childKey;
                var description = document.createElement("dd");
                description.appendChild(resultValue(value[childKey], childKey, expandAll));
                fields.appendChild(term);
                fields.appendChild(description);
            });
            return fields;
        }
        var text = document.createElement("span");
        text.textContent = value === null || value === undefined ? "-" : String(value);
        return text;
    }

    function resultItem(value, fallback, expandAll) {
        var item = document.createElement("li");
        item.className = "task-tree-item";
        if (value && typeof value === "object") {
            var details = document.createElement("details");
            details.open = expandAll || containsFailure(value);
            var summary = document.createElement("summary");
            summary.className = "task-tree-summary";
            summary.textContent = summaryLabel(value) || fallback;
            details.appendChild(summary);
            details.appendChild(resultValue(value, "", expandAll));
            item.appendChild(details);
        } else {
            item.textContent = String(value);
        }
        return item;
    }

    function renderResult(root, value, expandAll) {
        root.replaceChildren();
        if (value === null || value === undefined) {
            var empty = document.createElement("p");
            empty.className = "small text-muted mb-0";
            empty.textContent = "暂无结果数据";
            root.appendChild(empty);
            return;
        }
        root.appendChild(resultValue(value, "", expandAll));
    }

    function nodeStatus(result) {
        if (result.ssh_success === false) return {label: "SSH 失败", style: "bg-danger"};
        if (result.nginx_available === false) return {label: "未检测到 Nginx", style: "bg-warning text-dark"};
        if (result.nginx_available === true) return {label: "SSH / Nginx 正常", style: "bg-success"};
        if (result.ssh_success === true) return {label: "SSH 正常", style: "bg-success"};
        if (result.success === true || result.status === "success" || result.status === "online") {
            return {label: result.status === "online" ? "在线" : "成功", style: "bg-success"};
        }
        if (result.success === false || result.status === "failed" || result.status === "offline") {
            return {label: result.status === "offline" ? "离线" : "失败", style: "bg-danger"};
        }
        return {label: result.status || "完成", style: "bg-secondary"};
    }

    function renderNodeResults(root, nodes) {
        var expandedResults = Object.create(null);
        root.querySelectorAll(".task-node-result-details[open]").forEach(function (details) {
            if (details.dataset.resultKey) expandedResults[details.dataset.resultKey] = true;
        });
        root.replaceChildren();
        if (!nodes.length) {
            var empty = document.createElement("p");
            empty.className = "small text-muted mb-0";
            empty.textContent = "暂无节点结果";
            root.appendChild(empty);
            return;
        }
        var table = document.createElement("table");
        table.className = "table table-sm align-middle mb-0 task-node-result-table";
        var body = document.createElement("tbody");
        nodes.slice().sort(function (left, right) {
            return Number(left.ssh_success === true) - Number(right.ssh_success === true);
        }).forEach(function (result, index) {
            var row = document.createElement("tr");
            var nodeCell = document.createElement("td");
            nodeCell.className = "task-node-result-host";
            var host = document.createElement("strong");
            host.textContent = result.hostname || result.name || "节点 #" + (result.node_id || "-");
            nodeCell.appendChild(host);
            if (result.ip) {
                var address = document.createElement("small");
                address.className = "d-block text-muted font-monospace";
                address.textContent = result.ip;
                nodeCell.appendChild(address);
            }
            var statusCell = document.createElement("td");
            var status = nodeStatus(result);
            var badge = document.createElement("span");
            badge.className = "badge " + status.style;
            badge.textContent = status.label;
            statusCell.appendChild(badge);
            var messageCell = document.createElement("td");
            messageCell.className = "task-node-result-message";
            var message = result.message || result.detail || result.action || "-";
            if (typeof message !== "string") message = JSON.stringify(message);
            var details = document.createElement("details");
            details.className = "task-node-result-details";
            details.dataset.resultKey = String(
                result.node_id || result.hostname || result.ip || index
            );
            details.open = Boolean(expandedResults[details.dataset.resultKey]);
            var summary = document.createElement("summary");
            summary.className = "task-node-result-preview";
            summary.title = message;
            summary.textContent = message.length > 80 ? message.slice(0, 77) + "..." : message;
            var fullResult = document.createElement("div");
            fullResult.className = "task-node-result-full";
            fullResult.appendChild(resultValue(result, "", false));
            details.append(summary, fullResult);
            messageCell.appendChild(details);
            row.append(nodeCell, statusCell, messageCell);
            body.appendChild(row);
        });
        table.appendChild(body);
        root.appendChild(table);
    }

    function renderTaskResult(root, value, expandAll) {
        var nodeResults = value && Array.isArray(value.nodes) ? value.nodes : [];
        var hasNodeResults = nodeResults.length > 0 && nodeResults.every(function (item) {
            return item && typeof item === "object" &&
                (item.node_id !== undefined || item.hostname || item.ip);
        });
        if (hasNodeResults) {
            renderNodeResults(root, nodeResults);
            return;
        }
        renderResult(root, value, expandAll);
    }

    function initializeTargetToggle() {
        var button = document.getElementById("taskToggleTargets");
        var overflow = document.getElementById("taskTargetOverflow");
        if (!button || !overflow) return;
        button.addEventListener("click", function () {
            var expanded = button.getAttribute("aria-expanded") === "true";
            button.setAttribute("aria-expanded", expanded ? "false" : "true");
            overflow.hidden = expanded;
            button.textContent = expanded
                ? "展开其余 " + button.dataset.count + " 台"
                : "收起目标节点";
        });
    }

    function renderSummary(summary, value) {
        summary.replaceChildren();
        if (!value || typeof value !== "object") return;
        [["success", "bg-success", "成功"], ["failed", "bg-danger", "失败"], ["total", "bg-secondary", "共"]]
            .forEach(function (item) {
                if (value[item[0]] === undefined) return;
                var badge = document.createElement("span");
                badge.className = "badge " + item[1];
                badge.textContent = item[2] + " " + value[item[0]];
                summary.appendChild(badge);
            });
    }

    function formatLogMessage(message) {
        var formatted = String(message || "");
        var replacements = [];
        logTargetMap.slice().sort(function (left, right) {
            return String(right.ip || "").length - String(left.ip || "").length;
        }).forEach(function (target, index) {
            var hostname = String(target.hostname || "").trim();
            var ip = String(target.ip || "").trim();
            var label = hostname + " (" + ip + ")";
            if (!hostname || !ip) return;
            var token = "\u0000" + index + "\u0000";
            formatted = formatted.split(label).join(token);
            formatted = formatted.split(ip).join(token);
            replacements.push({token: token, label: label});
        });
        replacements.forEach(function (item) {
            formatted = formatted.split(item.token).join(item.label);
        });
        return formatted;
    }

    function findLogHostIp(message) {
        var formatted = String(message || "");
        for (var index = 0; index < logTargetMap.length; index += 1) {
            var target = logTargetMap[index];
            var hostname = String(target.hostname || "").trim();
            var ip = String(target.ip || "").trim();
            if (hostname && ip && formatted.indexOf(hostname + " (" + ip + ")") >= 0) {
                return ip;
            }
        }
        for (var fallbackIndex = 0; fallbackIndex < logTargetMap.length; fallbackIndex += 1) {
            var fallbackTarget = logTargetMap[fallbackIndex];
            var fallbackHostname = String(fallbackTarget.hostname || "").trim();
            var fallbackIp = String(fallbackTarget.ip || "").trim();
            if (fallbackHostname && formatted.indexOf("节点 " + fallbackHostname) >= 0) {
                return fallbackIp;
            }
        }
        return "";
    }

    function renderLog(log) {
        var row = document.createElement("div");
        row.className = "task-log-row log-level-" + (log.level || "info");
        var formattedMessage = formatLogMessage(log.message);
        row.dataset.logHostIp = findLogHostIp(formattedMessage);
        var time = document.createElement("time");
        time.textContent = log.created_at ? new Date(log.created_at).toLocaleString() : "";
        var message = document.createElement("span");
        message.className = "task-log-message";
        message.textContent = formattedMessage;
        row.append(time, message);
        return row;
    }

    function formatTaskTime(value) {
        return value ? new Date(value).toLocaleString() : "-";
    }

    function formatDuration(startedAt, finishedAt) {
        if (!startedAt || !finishedAt) return "";
        var seconds = Math.max(0, (new Date(finishedAt) - new Date(startedAt)) / 1000);
        return seconds >= 60 ? (seconds / 60).toFixed(1) + " 分钟" : seconds.toFixed(1) + " 秒";
    }

    async function cancelTask(button) {
        var taskId = button.getAttribute("data-task-id");
        var taskType = button.getAttribute("data-task-type") || "";
        window.showConfirm(
            "确认取消任务",
            "确定取消任务 #" + taskId + "（" + taskType + "）吗？任务会停止后续步骤；将尝试关闭当前 SSH 连接，已经在节点执行的远程命令可能继续运行，且不会自动回滚已变更文件。",
            async function () {
                button.disabled = true;
                try {
                    var response = await fetch(button.getAttribute("data-cancel-url"), {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {
                            "X-CSRFToken": document.querySelector("meta[name='csrf-token']").content,
                            "X-Requested-With": "XMLHttpRequest"
                        }
                    });
                    var payload = await response.json();
                    if (!response.ok || !payload.success) {
                        throw new Error(payload.message || "取消失败");
                    }
                    window.location.reload();
                } catch (error) {
                    button.disabled = false;
                    window.showAlert("取消失败", error.message || "取消请求失败");
                }
            }
        );
    }

    function initializeRowNavigation() {
        document.querySelectorAll(".task-center-row").forEach(function (row) {
            row.addEventListener("click", function (event) {
                if (event.target.closest("a, button, select, input")) return;
                window.location.href = row.getAttribute("data-detail-url");
            });
        });
    }

    function initializeListPolling() {
        var center = document.getElementById("taskCenter");
        if (!center || !document.querySelector(".task-center-row[data-task-active='true']")) return;
        var busy = false;

        async function poll() {
            if (busy) return;
            busy = true;
            try {
                var params = new URLSearchParams();
                params.set("page", center.getAttribute("data-page"));
                params.set("page_size", center.getAttribute("data-page-size"));
                var form = document.getElementById("taskCenterFilterForm");
                var search = form.querySelector("input[name='search']").value.trim();
                var operation = form.querySelector("select[name='operation_type']").value;
                var status = form.querySelector("select[name='status']").value;
                var filteredOut = false;
                if (search) params.set("search", search);
                if (operation) params.set("operation_type", operation);
                var response = await fetch("/api/tasks?" + params.toString(), { credentials: "same-origin" });
                if (!response.ok) return;
                var payload = await response.json();
                (payload.items || []).forEach(function (task) {
                    var row = document.querySelector(".task-center-row[data-task-id='" + task.id + "']");
                    if (!row) return;
                    updateProgress(row, task.progress);
                    updateStatus(row, task.status);
                    var summary = row.querySelector("[data-task-summary-secondary]");
                    if (summary && task.detail) summary.textContent = task.detail;
                    var upgradeCancel = row.querySelector(".task-cancel-button");
                    var cancellableUpgrade = task.status === "pending" || (task.status === "running" && /获取|检查 gcc|创建远程工作目录|上传 Nginx 源码包|解压 Nginx 源码包/.test(task.detail));
                    if (upgradeCancel && task.operation_type === "nginx_upgrade" && !cancellableUpgrade) upgradeCancel.remove();
                    if (upgradeCancel && task.operation_type === "nginx_rollback") upgradeCancel.remove();
                    if (status && task.status !== status) filteredOut = true;
                    if (terminalStatuses.indexOf(task.status) >= 0) {
                        row.setAttribute("data-task-active", "false");
                        var cancel = row.querySelector(".task-cancel-button");
                        if (cancel) cancel.remove();
                    }
                });
                if (filteredOut) window.location.reload();
            } catch (error) {
                // 轮询失败时保留页面当前数据，下一轮继续尝试。
            } finally {
                busy = false;
            }
        }
        window.setInterval(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
    }

    function initializeDetailPolling() {
        var detail = document.getElementById("taskDetail");
        if (!detail) return;
        var resultRoot = document.getElementById("taskResultTree");
        var summaryRoot = document.getElementById("taskResultSummary");
        var logList = document.getElementById("taskLogList");
        var logCount = document.querySelector("[data-log-count]");
        var loadMoreLogs = document.getElementById("taskLoadMoreLogs");
        var logHostFilter = document.getElementById("taskLogHostFilter");
        var cursor = Number(detail.getAttribute("data-log-cursor")) || 0;
        var active = detail.getAttribute("data-task-active") === "true";
        var isConfigSync = detail.getAttribute("data-is-config-sync") === "true";
        var isReleaseTask = detail.getAttribute("data-is-release-type") === "true";
        var isHostScopedLogTask = isConfigSync || isReleaseTask;
        var resultElement = document.getElementById("taskResultData");
        var labelsElement = document.getElementById("taskResultLabels");
        var logTargetsElement = document.getElementById("taskLogTargets");
        var initialResult = null;
        try { initialResult = JSON.parse(resultElement.textContent); } catch (error) {}
        try { fieldLabels = JSON.parse(labelsElement.textContent); } catch (error) {}
        try { logTargetMap = JSON.parse(logTargetsElement.textContent); } catch (error) {}
        logList.querySelectorAll(".task-log-row").forEach(function (row) {
            var message = row.querySelector(".task-log-message");
            message.textContent = formatLogMessage(message.textContent);
            row.dataset.logHostIp = findLogHostIp(message.textContent);
        });

        function applyLogHostFilter() {
            if (!isHostScopedLogTask || !logHostFilter) return;
            var selectedIp = logHostFilter.value;
            var rows = Array.prototype.slice.call(logList.querySelectorAll(".task-log-row"));
            var visibleCount = 0;
            rows.forEach(function (row) {
                var visible = !selectedIp || row.dataset.logHostIp === selectedIp;
                row.hidden = !visible;
                if (visible) visibleCount += 1;
            });
            var filterEmpty = logList.querySelector("[data-filter-empty]");
            if (!filterEmpty) {
                filterEmpty = document.createElement("p");
                filterEmpty.className = "small mb-0 task-log-filter-empty";
                filterEmpty.setAttribute("data-filter-empty", "");
                filterEmpty.textContent = "所选主机暂无执行日志";
                logList.appendChild(filterEmpty);
            }
            filterEmpty.hidden = !selectedIp || rows.length === 0 || visibleCount > 0;
            if (logCount) {
                logCount.textContent = (selectedIp ? visibleCount : rows.length) + " 条";
            }
        }

        if (logHostFilter) {
            logHostFilter.addEventListener("change", function () {
                applyLogHostFilter();
                loadAllLogsForFilter();
            });
            applyLogHostFilter();
        }
        renderTaskResult(
            resultRoot,
            initialResult,
            isConfigSync
        );
        renderSummary(summaryRoot, initialResult && initialResult.summary);
        if (isHostScopedLogTask) logList.scrollTop = logList.scrollHeight;
        var busy = false;
        if (loadMoreLogs) {
            loadMoreLogs.addEventListener("click", function () { poll(true); });
        }
        async function poll(loadHistory) {
            if (busy || (!active && !loadHistory)) return;
            busy = true;
            try {
                var url = detail.getAttribute("data-api-url") + "?after_log_id=" + cursor + "&log_limit=200";
                var response = await fetch(url, { credentials: "same-origin" });
                var payload = await response.json();
                if (!response.ok || payload.success === false) return;
                updateProgress(detail, payload.progress);
                updateStatus(detail, payload.status);
                var started = detail.querySelector("[data-task-started]");
                var finished = detail.querySelector("[data-task-finished]");
                var duration = detail.querySelector("[data-task-duration]");
                if (started && payload.started_at) started.textContent = formatTaskTime(payload.started_at);
                if (finished && payload.finished_at) finished.textContent = formatTaskTime(payload.finished_at);
                if (duration) {
                    var elapsed = formatDuration(payload.started_at, payload.finished_at);
                    duration.textContent = elapsed ? "耗时 " + elapsed : "";
                }
                var description = detail.querySelector("[data-task-detail]");
                if (description) description.textContent = payload.detail || "";
                var upgradeCancel = detail.querySelector(".task-cancel-button");
                var cancellableUpgrade = payload.status === "pending" || (payload.status === "running" && /获取|检查 gcc|创建远程工作目录|上传 Nginx 源码包|解压 Nginx 源码包/.test(payload.detail));
                if (upgradeCancel && payload.operation_type === "nginx_upgrade" && !cancellableUpgrade) upgradeCancel.remove();
                if (upgradeCancel && payload.operation_type === "nginx_rollback") upgradeCancel.remove();
                (payload.logs || []).forEach(function (log) {
                    var empty = logList.querySelector("[data-empty-logs]");
                    if (empty) empty.remove();
                    logList.appendChild(renderLog(log));
                    cursor = Math.max(cursor, Number(log.id) || 0);
                });
                if (logHostFilter) applyLogHostFilter();
                if (isHostScopedLogTask && (payload.logs || []).length) {
                    logList.scrollTop = logList.scrollHeight;
                }
                cursor = Math.max(cursor, Number(payload.next_log_id) || 0);
                detail.setAttribute("data-log-cursor", String(cursor));
                if (logCount && !logHostFilter) {
                    logCount.textContent = logList.querySelectorAll(".task-log-row").length + " 条";
                }
                if (loadMoreLogs) {
                    loadMoreLogs.hidden = !payload.has_more_logs;
                }
                if (payload.result_tree !== null && payload.result_tree !== undefined) {
                    renderTaskResult(
                        resultRoot,
                        payload.result_tree,
                        isConfigSync
                    );
                    renderSummary(summaryRoot, payload.result_tree.summary);
                }
                if (terminalStatuses.indexOf(payload.status) >= 0) {
                    active = false;
                    detail.setAttribute("data-task-active", "false");
                    var cancel = detail.querySelector(".task-cancel-button");
                    if (cancel) cancel.remove();
                }
            } catch (error) {
                // 轮询失败时保留已显示日志，并在下个周期重试。
            } finally {
                busy = false;
            }
        }

        async function loadAllLogsForFilter() {
            if (!logHostFilter || !logHostFilter.value || !loadMoreLogs) return;
            if (busy) {
                window.setTimeout(loadAllLogsForFilter, 100);
                return;
            }
            while (!loadMoreLogs.hidden) {
                var previousCursor = cursor;
                await poll(true);
                if (cursor === previousCursor) break;
            }
        }

        if (loadMoreLogs && !active) {
            loadMoreLogs.hidden = loadMoreLogs.dataset.hasMore !== "true";
        }
        if (active) window.setInterval(function () { poll(false); }, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll(".task-cancel-button").forEach(function (button) {
            button.addEventListener("click", function () { cancelTask(button); });
        });
        initializeTargetToggle();
        initializeRowNavigation();
        initializeListPolling();
        initializeDetailPolling();
    });
}());
