(function () {
    "use strict";

    var homeConfig = document.getElementById("serviceConfig");
    var taskConfig = document.getElementById("serviceTaskConfig");
    var terminal = { success: true, failed: true, cancelled: true };

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function csrfToken() {
        var token = document.querySelector('meta[name="csrf-token"]');
        return token ? token.content : "";
    }

    function statusLabel(status) {
        return {
            pending: "等待执行",
            running: "执行中",
            success: "成功",
            failed: "失败",
            cancelled: "已取消",
        }[status] || status || "未知";
    }

    function statusClass(status) {
        return {
            pending: "secondary",
            running: "info",
            success: "success",
            failed: "danger",
            cancelled: "secondary",
        }[status] || "secondary";
    }

    function actionLabel(action) {
        return { start: "启动", stop: "停止", reload: "重载", restart: "重启" }[action] || action;
    }

    function showError(payload, fallback) {
        var message = payload && payload.message ? payload.message : fallback;
        if (window.showAlert) window.showAlert("操作失败", message);
        else window.alert(message);
    }

    function initHome() {
        if (!homeConfig) return;
        var canOperate = homeConfig.dataset.canOperate === "true";
        var maxCount = Number(homeConfig.dataset.maxCount || 3);
        var selected = {};
        var draft = {};
        var nodeRows = document.getElementById("nodePickerRows");
        var searchInput = document.getElementById("nodeSearch");
        var searchTimer = null;
        var pollTimer = null;
        var activeTaskId = 0;
        var logCursor = 0;

        function updateActionButtons() {
            var enabled = canOperate && Object.keys(selected).length > 0;
            document.querySelectorAll("[data-service-action]").forEach(function (button) {
                button.disabled = !enabled;
            });
            document.getElementById("clearSelection").disabled = Object.keys(selected).length === 0;
        }

        function renderSelected() {
            var ids = Object.keys(selected);
            var chips = document.getElementById("selectedChips");
            var empty = document.getElementById("emptySelection");
            empty.classList.toggle("d-none", ids.length > 0);
            chips.replaceChildren();
            ids.forEach(function (id) {
                var node = selected[id];
                var chip = document.createElement("span");
                chip.className = "service-chip";
                chip.textContent = node.hostname + " · " + node.ip;
                chips.appendChild(chip);
            });
            document.getElementById("selectNodeLabel").textContent = ids.length
                ? "选择节点（已选 " + ids.length + "）"
                : "选择节点";
            updateActionButtons();
        }

        function syncDraftCount() {
            document.getElementById("nodePickerCount").textContent =
                "共 " + nodeRows.querySelectorAll("tr[data-node-id]").length + " 台 · 已选 " + Object.keys(draft).length + " 台";
        }

        function renderNodeRows(nodes, total) {
            nodeRows.replaceChildren();
            if (!nodes.length) {
                var emptyRow = document.createElement("tr");
                emptyRow.innerHTML = '<td colspan="5" class="text-center text-muted py-4">没有匹配的节点</td>';
                nodeRows.appendChild(emptyRow);
            }
            nodes.forEach(function (node) {
                var row = document.createElement("tr");
                row.dataset.nodeId = node.id;
                if (!node.can_select) row.className = "text-muted";
                var checked = !!draft[String(node.id)];
                var disabled = !node.can_select || (!checked && Object.keys(draft).length >= maxCount);
                var nginxStatus = node.nginx_available === true
                    ? (node.nginx_version || "已检测")
                    : "未检测到";
                var state = node.can_select ? "可操作" : node.disabled_reason;
                row.innerHTML =
                    '<td class="text-center"><input type="checkbox" class="form-check-input service-node-checkbox" data-id="' + node.id + '"' +
                    (checked ? " checked" : "") + (disabled ? " disabled" : "") + ' aria-label="选择 ' + escapeHtml(node.hostname) + '"></td>' +
                    '<td><span class="service-picker-node">' + escapeHtml(node.hostname) + '<small>' + escapeHtml(node.ip) + ':' + node.port + '</small></span></td>' +
                    '<td>' + escapeHtml((node.groups || []).join(", ") || "-") + '</td>' +
                    '<td>' + escapeHtml(nginxStatus) + '</td>' +
                    '<td><span class="badge text-bg-' + (node.can_select ? "success" : "secondary") + '" title="' + escapeHtml(state) + '">' + escapeHtml(state) + '</span></td>';
                row.dataset.node = JSON.stringify(node);
                nodeRows.appendChild(row);
            });
            document.getElementById("nodePickerLimit").textContent =
                "最多 " + maxCount + " 台" + (total > nodes.length ? " · 当前显示 " + nodes.length + " / " + total : "");
            syncDraftCount();
            var enabledBoxes = nodeRows.querySelectorAll(".service-node-checkbox:not(:disabled)");
            document.getElementById("selectVisible").checked = enabledBoxes.length > 0 &&
                Array.prototype.every.call(enabledBoxes, function (box) { return box.checked; });
        }

        function loadNodes() {
            var url = new URL(homeConfig.dataset.nodesUrl, window.location.origin);
            if (searchInput.value.trim()) url.searchParams.set("search", searchInput.value.trim());
            nodeRows.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">正在加载节点</td></tr>';
            fetch(url.toString(), { headers: { Accept: "application/json" } })
                .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
                .then(function (result) {
                    if (!result.response.ok || !result.data.success) {
                        showError(result.data, "无法读取节点");
                        return;
                    }
                    renderNodeRows(result.data.data || [], result.data.total || 0);
                })
                .catch(function () { showError(null, "网络错误，无法读取节点"); });
        }

        document.getElementById("nodePickerModal").addEventListener("show.bs.modal", function () {
            draft = Object.assign({}, selected);
            searchInput.value = "";
            loadNodes();
        });
        searchInput.addEventListener("input", function () {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(loadNodes, 250);
        });
        nodeRows.addEventListener("change", function (event) {
            var checkbox = event.target.closest(".service-node-checkbox");
            if (!checkbox) return;
            var row = checkbox.closest("tr");
            var node = JSON.parse(row.dataset.node);
            if (checkbox.checked) {
                if (Object.keys(draft).length >= maxCount) {
                    checkbox.checked = false;
                    if (window.showToast) window.showToast("最多选择 " + maxCount + " 台节点", "warning");
                    return;
                }
                draft[String(node.id)] = node;
            } else {
                delete draft[String(node.id)];
            }
            renderNodeRows(
                Array.prototype.map.call(nodeRows.querySelectorAll("tr[data-node-id]"), function (item) {
                    return JSON.parse(item.dataset.node);
                }),
                nodeRows.querySelectorAll("tr[data-node-id]").length
            );
        });
        document.getElementById("selectVisible").addEventListener("change", function (event) {
            var boxes = nodeRows.querySelectorAll(".service-node-checkbox:not(:disabled)");
            Array.prototype.forEach.call(boxes, function (checkbox) {
                var row = checkbox.closest("tr");
                var node = JSON.parse(row.dataset.node);
                if (event.target.checked && Object.keys(draft).length < maxCount) {
                    draft[String(node.id)] = node;
                    checkbox.checked = true;
                } else if (!event.target.checked) {
                    delete draft[String(node.id)];
                    checkbox.checked = false;
                }
            });
            renderNodeRows(
                Array.prototype.map.call(nodeRows.querySelectorAll("tr[data-node-id]"), function (item) {
                    return JSON.parse(item.dataset.node);
                }),
                nodeRows.querySelectorAll("tr[data-node-id]").length
            );
        });
        document.getElementById("confirmNodeSelection").addEventListener("click", function () {
            selected = Object.assign({}, draft);
            renderSelected();
            bootstrap.Modal.getInstance(document.getElementById("nodePickerModal")).hide();
        });
        document.getElementById("clearSelection").addEventListener("click", function () {
            selected = {};
            renderSelected();
        });

        function renderProgress(data) {
            document.getElementById("progressPlaceholder").classList.add("d-none");
            document.getElementById("progressLive").classList.remove("d-none");
            document.getElementById("batchNumberLabel").textContent = "批次 " + data.batch_number;
            var progress = Number(data.progress || 0);
            var badge = document.getElementById("progressPercent");
            badge.textContent = progress + "%";
            badge.className = "badge text-bg-" + statusClass(data.status) + (terminal[data.status] ? "" : "");
            var bar = document.getElementById("progressBar");
            bar.style.width = progress + "%";
            bar.classList.toggle("progress-bar-animated", !terminal[data.status]);
            bar.classList.toggle("bg-danger", data.status === "failed");
            bar.classList.toggle("bg-success", data.status === "success");
            document.getElementById("progressDetail").textContent = data.detail || "";
            document.getElementById("taskLogLink").href = "/nginx/service/task/" + data.task_id + "/log/";
            var container = document.getElementById("serviceNodesProgress");
            container.replaceChildren();
            var nodes = data.result_tree && data.result_tree.nodes ? data.result_tree.nodes : [];
            nodes.forEach(function (node) {
                var item = document.createElement("div");
                item.className = "service-node-item";
                var header = document.createElement("div");
                header.className = "service-node-item-header";
                var identity = document.createElement("strong");
                identity.className = "small";
                identity.textContent = node.hostname + (node.ip ? " (" + node.ip + ")" : "");
                var badgeNode = document.createElement("span");
                badgeNode.className = "badge text-bg-" + statusClass(node.status);
                badgeNode.textContent = statusLabel(node.status);
                var message = document.createElement("span");
                message.className = "service-node-message";
                message.textContent = node.message || "";
                header.appendChild(identity);
                header.appendChild(badgeNode);
                item.appendChild(header);
                item.appendChild(message);
                container.appendChild(item);
            });
        }

        function pollTask(taskId, batchNumber) {
            activeTaskId = taskId;
            logCursor = 0;
            document.getElementById("progressPlaceholder").classList.add("d-none");
            document.getElementById("progressLive").classList.remove("d-none");
            document.getElementById("batchNumberLabel").textContent = "批次 " + batchNumber;
            document.getElementById("taskLogLink").href = "/nginx/service/task/" + taskId + "/log/";
            function poll() {
                if (activeTaskId !== taskId) return;
                var url = homeConfig.dataset.taskUrlPrefix + taskId + "?after_log_id=" + logCursor;
                fetch(url, { headers: { Accept: "application/json" } })
                    .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
                    .then(function (result) {
                        if (!result.response.ok || !result.data.success) return;
                        renderProgress(result.data);
                        logCursor = result.data.next_log_id || logCursor;
                        if (!terminal[result.data.status]) pollTimer = setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                    })
                    .catch(function () { pollTimer = setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000); });
            }
            clearTimeout(pollTimer);
            poll();
        }

        function submitAction(action) {
            var labels = { start: "启动", stop: "停止", reload: "重载", restart: "重启" };
            var names = Object.keys(selected).map(function (id) { return selected[id].hostname; });
            var risky = action === "stop" || action === "restart";
            var body = "<p>将对以下 <strong>" + names.length + "</strong> 台节点执行 <strong>" + labels[action] + "</strong> Nginx：</p><p><code>" + escapeHtml(names.join("、")) + "</code></p>";
            body += action === "reload"
                ? '<p class="small text-muted mb-0">平滑重载配置，通常不会中断已有连接。</p>'
                : (risky ? '<p class="small text-danger mb-0">此操作可能中断业务流量，请确认维护窗口。</p>' : "");
            window.showConfirm("确认" + labels[action] + " Nginx", body, function () {
                document.querySelectorAll("[data-service-action]").forEach(function (button) { button.disabled = true; });
                fetch(homeConfig.dataset.executeUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken(),
                        "X-Requested-With": "XMLHttpRequest",
                        Accept: "application/json",
                    },
                    body: JSON.stringify({ action: action, node_ids: Object.keys(selected).map(Number) }),
                })
                    .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
                    .then(function (result) {
                        if (!result.response.ok || !result.data.success) {
                            showError(result.data, "无法创建启停任务");
                            updateActionButtons();
                            return;
                        }
                        if (result.data.skipped && result.data.skipped.length) {
                            var skipped = result.data.skipped.map(function (node) {
                                return (node.hostname || ("节点 " + node.id)) + "：" + node.reason;
                            }).join("\n");
                            if (window.showToast) window.showToast(result.data.message + "\n" + skipped, "warning");
                        } else if (window.showToast) {
                            window.showToast(result.data.message, "success");
                        }
                        pollTask(result.data.task_id, result.data.batch_number);
                    })
                    .catch(function () { showError(null, "网络错误，无法创建启停任务"); updateActionButtons(); });
            }, true, risky ? "md" : "sm");
        }

        document.querySelectorAll("[data-service-action]").forEach(function (button) {
            button.addEventListener("click", function () { submitAction(button.dataset.serviceAction); });
        });
        renderSelected();
        var activeTask = document.getElementById("progressLive").dataset.taskId;
        if (activeTask) pollTask(Number(activeTask), "");
    }

    function initTaskDetail() {
        if (!taskConfig || terminal[taskConfig.dataset.status]) return;
        var cursor = Number(taskConfig.dataset.logCursor || 0);
        var timer = null;
        var log = document.getElementById("serviceLog");
        var url = taskConfig.dataset.url;
        function renderResultTree(tree) {
            var tbody = document.getElementById("taskNodeResults");
            if (!tree || !tree.nodes || !tree.nodes.length) return;
            tbody.replaceChildren();
            tree.nodes.forEach(function (node) {
                var row = document.createElement("tr");
                [node.hostname, node.ip, statusLabel(node.status), node.message].forEach(function (value, index) {
                    var cell = document.createElement("td");
                    cell.textContent = value || "";
                    if (index === 1) cell.className = "font-monospace";
                    row.appendChild(cell);
                });
                tbody.appendChild(row);
            });
        }
        function poll() {
            var pollUrl = url + "?after_log_id=" + cursor;
            fetch(pollUrl, { headers: { Accept: "application/json" } })
                .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
                .then(function (result) {
                    if (!result.response.ok || !result.data.success) return;
                    var data = result.data;
                    var status = document.getElementById("taskStatus");
                    status.className = "badge text-bg-" + statusClass(data.status);
                    status.textContent = statusLabel(data.status);
                    document.getElementById("taskProgress").textContent = data.progress + "%";
                    document.getElementById("taskProgress").classList.toggle("d-none", !!terminal[data.status]);
                    document.getElementById("taskDetail").textContent = data.detail || "";
                    renderResultTree(data.result_tree);
                    (data.logs || []).forEach(function (entry) {
                        if (log.textContent === "暂无日志") log.textContent = "";
                        var stamp = entry.created_at ? entry.created_at.slice(11, 19) : "--:--:--";
                        log.textContent += "[" + stamp + "] " + entry.message + "\n";
                    });
                    cursor = data.next_log_id || cursor;
                    taskConfig.dataset.status = data.status;
                    if (!terminal[data.status]) timer = setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                })
                .catch(function () { timer = setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000); });
        }
        poll();
        window.addEventListener("beforeunload", function () { clearTimeout(timer); });
    }

    initHome();
    initTaskDetail();
})();
