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
        var maxCount = Number(homeConfig.dataset.maxCount) || 1;
        var selected = {};
        var draft = {};
        var allNodes = [];
        var nodesLoaded = false;
        var nodeRows = document.getElementById("nodePickerRows");
        var searchInput = document.getElementById("nodeSearch");
        var pageSizeInput = document.getElementById("serviceNodePageSize");
        var page = 1;
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
                var chip = document.createElement("div");
                chip.className = "upgrade-selected-chip";
                var label = document.createElement("span");
                label.textContent = node.hostname + " (" + node.ip + ")";
                var remove = document.createElement("button");
                remove.type = "button";
                remove.className = "upgrade-chip-remove";
                remove.title = "移除节点";
                remove.setAttribute("aria-label", "移除节点 " + node.hostname);
                remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
                remove.addEventListener("click", function () {
                    delete selected[id];
                    delete draft[id];
                    renderSelected();
                    renderNodePage();
                });
                chip.append(label, remove);
                chips.appendChild(chip);
            });
            document.getElementById("selectNodeLabel").textContent = ids.length
                ? "选择节点（已选 " + ids.length + "）"
                : "选择节点";
            updateActionButtons();
        }

        function searchTerms() {
            var value = window.getQueryTagValue
                ? window.getQueryTagValue("#nodeSearch")
                : searchInput.value;
            return value.split(/[,，]/).map(function (term) {
                return term.trim().toLocaleLowerCase();
            }).filter(Boolean);
        }

        function matchingNodes() {
            var terms = searchTerms();
            return allNodes.filter(function (node) {
                var searchable = [node.hostname, node.ip, node.groups.join(" ")]
                    .join(" ").toLocaleLowerCase();
                return terms.every(function (term) { return searchable.indexOf(term) >= 0; });
            });
        }

        function renderNodePage() {
            var matches = matchingNodes();
            var pageSize = Number(pageSizeInput.value) || 10;
            var pages = Math.max(1, Math.ceil(matches.length / pageSize));
            page = Math.min(page, pages);
            var start = (page - 1) * pageSize;
            var visibleNodes = matches.slice(start, start + pageSize);
            nodeRows.replaceChildren();
            if (!visibleNodes.length) {
                var emptyRow = document.createElement("tr");
                emptyRow.innerHTML = '<td colspan="5"><div class="empty-state py-3"><i class="bi bi-inbox" aria-hidden="true"></i><p>没有满足启停条件的节点</p></div></td>';
                nodeRows.appendChild(emptyRow);
            }
            visibleNodes.forEach(function (node) {
                var row = document.createElement("tr");
                row.dataset.nodeId = node.id;
                row.className = "upgrade-node-row";
                var checked = !!draft[String(node.id)];
                var disabled = !checked && Object.keys(draft).length >= maxCount;
                var environment = { dev: "开发", test: "测试", prod: "生产" }[node.environment] || node.environment;
                row.innerHTML =
                    '<td class="text-center"><input class="form-check-input upgrade-node-check service-node-checkbox" type="checkbox" value="' + node.id + '"' +
                    (checked ? " checked" : "") + (disabled ? " disabled" : "") + ' aria-label="选择 ' + escapeHtml(node.hostname) + '"></td>' +
                    '<td><span class="upgrade-node-name-address" title="' + escapeHtml(node.hostname + " " + node.ip + ":" + node.port) + '"><span class="upgrade-node-hostname">' + escapeHtml(node.hostname) + '</span><small class="text-muted ms-2 upgrade-node-address">' + escapeHtml(node.ip + ":" + node.port) + '</small></span></td>' +
                    '<td class="text-nowrap">' + escapeHtml(environment) + '</td>' +
                    '<td class="text-nowrap"><span class="badge text-bg-success">' + escapeHtml(node.nginx_version || "已检测") + '</span></td>' +
                    '<td><small class="upgrade-node-groups" title="' + escapeHtml(node.groups.join("、") || "-") + '">' + escapeHtml(node.groups.join("、") || "-") + '</small></td>';
                row.dataset.node = JSON.stringify(node);
                row.dataset.search = [node.hostname, node.ip, node.groups.join(" ")].join(" ").toLocaleLowerCase();
                row.addEventListener("click", function (event) {
                    if (event.target.closest("input, button, a, label, select")) return;
                    var checkbox = row.querySelector(".service-node-checkbox");
                    if (checkbox.disabled && !checkbox.checked) return;
                    checkbox.checked = !checkbox.checked;
                    checkbox.dispatchEvent(new Event("change", { bubbles: true }));
                });
                nodeRows.appendChild(row);
            });
            document.getElementById("serviceNodeResultCount").textContent =
                "共 " + matches.length + " 条 · 已选 " + Object.keys(draft).length + " 台";
            document.getElementById("nodePickerLimit").textContent = "最多 " + maxCount + " 台";
            document.getElementById("serviceNodePageLabel").textContent = page + " / " + pages;
            document.getElementById("serviceNodePageFirst").disabled = page <= 1;
            document.getElementById("serviceNodePagePrevious").disabled = page <= 1;
            document.getElementById("serviceNodePageNext").disabled = page >= pages;
            document.getElementById("serviceNodePageLast").disabled = page >= pages;
            var enabledBoxes = nodeRows.querySelectorAll(".service-node-checkbox:not(:disabled)");
            document.getElementById("selectVisible").checked = enabledBoxes.length > 0 &&
                Array.prototype.every.call(enabledBoxes, function (box) { return box.checked; });
        }

        function loadNodes() {
            nodeRows.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">正在加载节点</td></tr>';
            fetch(homeConfig.dataset.nodesUrl, { headers: { Accept: "application/json" } })
                .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
                .then(function (result) {
                    if (!result.response.ok || !result.data.success) {
                        showError(result.data, "无法读取节点");
                        return;
                    }
                    allNodes = (result.data.data || []).map(function (node) {
                        node.groups = node.groups || [];
                        return node;
                    });
                    maxCount = Number(result.data.batch_max_count) || maxCount;
                    homeConfig.dataset.maxCount = String(maxCount);
                    nodesLoaded = true;
                    page = 1;
                    renderNodePage();
                })
                .catch(function () { showError(null, "网络错误，无法读取节点"); });
        }

        document.getElementById("nodePickerModal").addEventListener("show.bs.modal", function () {
            draft = Object.assign({}, selected);
            if (searchInput.__ngxopsQueryTags) searchInput.__ngxopsQueryTags.clear();
            else searchInput.value = "";
            page = 1;
            if (nodesLoaded) renderNodePage();
            else loadNodes();
        });
        searchInput.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") return;
            event.preventDefault();
            if (window.commitQueryTagValue) window.commitQueryTagValue("#nodeSearch");
            page = 1;
            renderNodePage();
        });
        if (window.jQuery) {
            window.jQuery(searchInput).on("querytags:change", function () {
                page = 1;
                renderNodePage();
            });
        }
        pageSizeInput.addEventListener("change", function () { page = 1; renderNodePage(); });
        document.getElementById("serviceNodePageFirst").addEventListener("click", function () { page = 1; renderNodePage(); });
        document.getElementById("serviceNodePagePrevious").addEventListener("click", function () { page = Math.max(1, page - 1); renderNodePage(); });
        document.getElementById("serviceNodePageNext").addEventListener("click", function () { page += 1; renderNodePage(); });
        document.getElementById("serviceNodePageLast").addEventListener("click", function () {
            page = Math.max(1, Math.ceil(matchingNodes().length / (Number(pageSizeInput.value) || 10)));
            renderNodePage();
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
            renderNodePage();
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
            renderNodePage();
        });
        document.getElementById("confirmNodeSelection").addEventListener("click", function () {
            selected = Object.assign({}, draft);
            renderSelected();
            bootstrap.Modal.getInstance(document.getElementById("nodePickerModal")).hide();
        });
        document.getElementById("clearSelection").addEventListener("click", function () {
            selected = {};
            draft = {};
            renderSelected();
            renderNodePage();
        });

        function pollTask(taskId) {
            activeTaskId = taskId;
            logCursor = 0;
            function poll() {
                if (activeTaskId !== taskId) return;
                var url = homeConfig.dataset.taskUrlPrefix + taskId + "?after_log_id=" + logCursor;
                fetch(url, { headers: { Accept: "application/json" } })
                    .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
                    .then(function (result) {
                        if (!result.response.ok || !result.data.success) return;
                        logCursor = result.data.next_log_id || logCursor;
                        if (!terminal[result.data.status]) {
                            pollTimer = setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                        } else {
                            updateActionButtons();
                        }
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
                        if (window.showToast) {
                            window.showToast(
                                "Nginx " + labels[action] + "任务已创建",
                                "success",
                                6000,
                                {
                                    href: "/nginx/service/task/" + result.data.task_id + "/log/",
                                    label: "查看完整日志",
                                    target: "_blank",
                                }
                            );
                        }
                        pollTask(result.data.task_id);
                    })
                    .catch(function () { showError(null, "网络错误，无法创建启停任务"); updateActionButtons(); });
            }, true, risky ? "md" : "sm");
        }

        document.querySelectorAll("[data-service-action]").forEach(function (button) {
            button.addEventListener("click", function () { submitAction(button.dataset.serviceAction); });
        });
        renderSelected();
        var activeTask = homeConfig.dataset.activeTaskId;
        if (activeTask) pollTask(Number(activeTask), "");
    }

    function initServiceTargetToggle() {
        var toggle = document.getElementById("serviceTargetToggle");
        if (!toggle) return;
        var extras = document.querySelectorAll("[data-service-extra-target]");
        var label = toggle.querySelector("[data-target-toggle-label]");
        var icon = toggle.querySelector("i");
        toggle.addEventListener("click", function () {
            var expanded = toggle.getAttribute("aria-expanded") !== "true";
            extras.forEach(function (target) { target.hidden = !expanded; });
            toggle.setAttribute("aria-expanded", String(expanded));
            label.textContent = expanded
                ? "收起详情"
                : "展开详情（还有 " + toggle.dataset.extraCount + " 台）";
            icon.classList.toggle("bi-chevron-down", !expanded);
            icon.classList.toggle("bi-chevron-up", expanded);
        });
    }

    function initTaskDetail() {
        if (!taskConfig) return;
        var cursor = Number(taskConfig.dataset.logCursor || 0);
        var timer = null;
        var log = document.getElementById("serviceLog");
        var logFilter = document.getElementById("serviceLogHostFilter");
        var url = taskConfig.dataset.url;
        function renderLog(entry) {
            var empty = log.querySelector("[data-empty-log]");
            if (empty) empty.remove();
            var row = document.createElement("div");
            row.className = "upgrade-log-line log-" + (entry.level || "info");
            row.dataset.nodeId = entry.node_id == null ? "" : String(entry.node_id);
            var time = document.createElement("time");
            time.textContent = entry.created_at ? window.formatUtc8DateTime(entry.created_at) : "";
            var message = document.createElement("span");
            message.className = "upgrade-log-message";
            message.textContent = entry.message || "";
            row.append(time, message);
            log.appendChild(row);
        }
        function applyLogFilter() {
            if (!logFilter) return;
            var selected = logFilter.value;
            var rows = log.querySelectorAll(".upgrade-log-line");
            var visibleCount = 0;
            rows.forEach(function (row) {
                var visible = !selected || row.dataset.nodeId === selected;
                row.hidden = !visible;
                if (visible) visibleCount += 1;
            });
            var empty = log.querySelector("[data-filter-empty]");
            if (!empty) {
                empty = document.createElement("div");
                empty.className = "upgrade-log-empty";
                empty.dataset.filterEmpty = "";
                empty.textContent = "所选节点暂无执行日志";
                log.appendChild(empty);
            }
            empty.hidden = !selected || visibleCount > 0;
        }
        function renderResultTree(tree) {
            var tbody = document.getElementById("taskNodeResults");
            if (!tree || !tree.nodes || !tree.nodes.length) return;
            tbody.replaceChildren();
            tree.nodes.forEach(function (node) {
                var row = document.createElement("tr");
                [node.hostname, node.ip, node.message].forEach(function (value, index) {
                    var cell = document.createElement("td");
                    cell.textContent = value || "";
                    if (index === 1) cell.className = "font-monospace";
                    row.appendChild(cell);
                });
                tbody.appendChild(row);
            });
        }
        if (logFilter) logFilter.addEventListener("change", applyLogFilter);
        applyLogFilter();
        if (terminal[taskConfig.dataset.status]) return;
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
                    var progressBar = document.getElementById("taskProgressBar");
                    progressBar.style.width = data.progress + "%";
                    progressBar.parentElement.setAttribute("aria-valuenow", String(data.progress));
                    progressBar.classList.remove("bg-success", "bg-danger", "bg-secondary");
                    if (terminal[data.status]) progressBar.classList.add("bg-" + statusClass(data.status));
                    document.getElementById("taskProgress").textContent = data.progress + "%";
                    document.getElementById("taskDetail").textContent = data.detail || "";
                    document.getElementById("taskProgressLabel").textContent = data.progress + "% · " + (data.detail || "");
                    if (data.started_at) document.getElementById("startedAt").textContent = window.formatUtc8DateTime(data.started_at);
                    if (data.finished_at) document.getElementById("finishedAt").textContent = window.formatUtc8DateTime(data.finished_at);
                    renderResultTree(data.result_tree);
                    (data.logs || []).forEach(renderLog);
                    applyLogFilter();
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
    initServiceTargetToggle();
    initTaskDetail();
})();
