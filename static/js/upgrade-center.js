(function ($, window, document) {
    "use strict";

    var center = document.getElementById("upgradeStepper");
    var detail = document.getElementById("upgradeTaskDetail");
    var batchMax = center ? Number(center.dataset.batchMax) || 3 : 3;
    var token = document.querySelector("meta[name='csrf-token']");
    var csrfToken = token ? token.content : "";

    function requestJson(method, url, payload) {
        return $.ajax({
            method: method,
            url: url,
            contentType: "application/json",
            dataType: "json",
            headers: method === "GET" ? {} : { "X-CSRFToken": csrfToken },
            data: payload === undefined ? undefined : JSON.stringify(payload)
        });
    }

    function errorMessage(error) {
        return error && error.responseJSON && error.responseJSON.message
            ? error.responseJSON.message
            : "请求失败，请检查输入后重试";
    }

    function notify(message, type) {
        if (window.showToast) window.showToast(message, type || "info");
    }

    function activateStep(step) {
        document.querySelectorAll(".upgrade-panel").forEach(function (panel) {
            panel.classList.toggle("d-none", Number(panel.dataset.step) !== step);
        });
        document.querySelectorAll(".upgrade-step").forEach(function (item) {
            var itemStep = Number(item.dataset.stepTarget);
            item.classList.toggle("active", itemStep === step);
            item.classList.toggle("done", itemStep < step);
        });
        if (step === 3) fetchConfigureBaselines(false);
        window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function selectedNodeIds() {
        return Array.from(document.querySelectorAll(".upgrade-node-check:checked")).map(function (input) {
            return Number(input.value);
        });
    }

    function selectedNodes() {
        var selected = new Set(selectedNodeIds());
        return Array.from(document.querySelectorAll(".upgrade-node-row")).filter(function (row) {
            var checkbox = row.querySelector(".upgrade-node-check");
            return checkbox && selected.has(Number(checkbox.value));
        }).map(function (row) {
            var checkbox = row.querySelector(".upgrade-node-check");
            return { id: Number(checkbox.value), name: row.querySelector("strong").textContent, ip: row.querySelector("small").textContent.split(":")[0] };
        });
    }

    function selectedPackage() {
        var input = document.querySelector(".upgrade-package-check:checked");
        if (!input) return null;
        var row = input.closest(".upgrade-package-row");
        if (!row || row.classList.contains("d-none")) return null;
        return { id: Number(input.value), version: input.dataset.version, name: input.dataset.name };
    }

    function updateSelection() {
        var checks = Array.from(document.querySelectorAll(".upgrade-node-check"));
        var selected = selectedNodeIds();
        checks.forEach(function (input) {
            input.disabled = !input.checked && selected.length >= batchMax;
        });
        document.getElementById("stepOneCount").textContent = "已选 " + selected.length + " 个节点";
        document.getElementById("selectedNodeSummary").textContent = selected.length
            ? selectedNodes().map(function (node) { return node.name + " (" + node.ip + ")"; }).join("、")
            : "尚未选择节点";
        document.getElementById("stepOneNext").disabled = selected.length === 0 || !selectedPackage();
    }

    function filterTable(inputId, rowSelector) {
        var search = document.getElementById(inputId);
        search.addEventListener("input", function () {
            var query = search.value.trim().toLowerCase();
            document.querySelectorAll(rowSelector).forEach(function (row) {
                row.classList.toggle("d-none", query && !row.dataset.search.includes(query));
            });
            updateSelection();
        });
    }

    function currentMode() {
        var selected = document.querySelector("input[name='upgradeMode']:checked");
        return selected ? selected.value : "upgrade";
    }

    function currentWorkDir() {
        return document.getElementById("remoteWorkDir").value.trim() || "/tmp/nginx-upgrade";
    }

    function readBaselines() {
        var result = {};
        Object.keys(window.upgradeBaselines || {}).forEach(function (key) {
            result[key] = window.upgradeBaselines[key];
        });
        return result;
    }

    function fetchConfigureBaselines(force) {
        var ids = selectedNodeIds();
        var current = readBaselines();
        if (!force && ids.every(function (id) { return current[id] && current[id].ok; })) {
            renderFetchResults();
            return;
        }
        window.upgradeBaselines = {};
        var results = document.getElementById("nodeFetchResults");
        results.replaceChildren();
        document.getElementById("fetchStatus").textContent = "正在逐台读取 nginx -V，请保持页面打开…";
        document.getElementById("stepThreeNext").disabled = true;
        var queue = ids.map(function (id) {
            return requestJson("POST", "/api/upgrade/nodes/" + id + "/nginx-v", {});
        });
        $.when.apply($, queue).done(function () {
            if (queue.length === 1) {
                saveBaseline(ids[0], arguments[0]);
            } else {
                queue.forEach(function (_request, index) {
                    saveBaseline(ids[index], arguments[index][0]);
                });
            }
            renderFetchResults();
        }).fail(function (error) {
            if (queue.length === 1) {
                window.upgradeBaselines[ids[0]] = { ok: false, message: errorMessage(error) };
            } else {
                queue.forEach(function (item, index) {
                    if (item.state() === "rejected") {
                        window.upgradeBaselines[ids[index]] = { ok: false, message: errorMessage(item) };
                    }
                });
            }
            renderFetchResults();
        });
    }

    function saveBaseline(nodeId, response) {
        window.upgradeBaselines[nodeId] = response && response.data
            ? { ok: true, data: response.data }
            : { ok: false, message: "nginx -V 未返回参数" };
    }

    function renderFetchResults() {
        var selected = selectedNodes();
        var results = document.getElementById("nodeFetchResults");
        results.replaceChildren();
        selected.forEach(function (node) {
            var baseline = window.upgradeBaselines[node.id];
            var row = document.createElement("div");
            row.className = "upgrade-fetch-row" + (baseline && baseline.ok ? "" : " failed");
            var identity = document.createElement("strong");
            identity.textContent = node.name + " · " + node.ip;
            var result = document.createElement("span");
            result.textContent = baseline && baseline.ok
                ? "Nginx " + (baseline.data.version || "版本未知")
                : (baseline ? baseline.message : "等待读取");
            row.append(identity, result);
            results.appendChild(row);
        });
        var baselines = selected.map(function (node) { return window.upgradeBaselines[node.id]; });
        var allReady = baselines.length > 0 && baselines.every(function (item) { return item && item.ok; });
        document.getElementById("fetchStatus").textContent = allReady
            ? "已读取全部节点的编译参数。"
            : "读取未完成；请检查节点连接后重新读取。";
        document.getElementById("stepThreeNext").disabled = !allReady;
        if (!allReady) return;
        var signatures = baselines.map(function (item) {
            return item.data.params.slice().sort().join("\n");
        });
        document.getElementById("paramsMismatchNotice").classList.toggle(
            "d-none",
            signatures.every(function (value) { return value === signatures[0]; })
        );
        renderBuiltinModules(baselines[0].data.params);
    }

    function renderBuiltinModules(currentParams) {
        var list = document.getElementById("builtinModuleList");
        var selected = new Set(currentParams);
        list.replaceChildren();
        (window.NGXOPS_BUILTIN_MODULES || []).forEach(function (flag) {
            var label = document.createElement("label");
            label.className = "upgrade-module-option";
            var checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "form-check-input mt-1 builtin-module-check";
            checkbox.value = flag;
            checkbox.checked = selected.has(flag);
            checkbox.addEventListener("change", updateModuleCounts);
            var text = document.createElement("code");
            text.textContent = flag;
            label.append(checkbox, text);
            list.appendChild(label);
        });
        filterModuleList();
    }

    function filterModuleList() {
        var search = document.getElementById("moduleSearch").value.trim().toLowerCase();
        document.querySelectorAll(".upgrade-module-option").forEach(function (row) {
            row.classList.toggle("d-none", search && !row.textContent.toLowerCase().includes(search));
        });
    }

    function collectModuleChanges() {
        var reference = selectedNodes()[0];
        var baseline = reference && window.upgradeBaselines[reference.id];
        var current = new Set(baseline && baseline.ok ? baseline.data.params : []);
        var added = [];
        var removed = [];
        document.querySelectorAll(".builtin-module-check").forEach(function (input) {
            if (input.checked && !current.has(input.value)) added.push(input.value);
            if (!input.checked && current.has(input.value)) removed.push(input.value);
        });
        return { added: added, removed: removed };
    }

    function updateModuleCounts() {
        var changes = collectModuleChanges();
        var count = document.getElementById("stepThreeNext");
        count.title = "新增 " + changes.added.length + " 项，移除 " + changes.removed.length + " 项";
    }

    function addThirdPartyRow() {
        var template = document.getElementById("thirdPartyTemplate");
        var fragment = template.content.cloneNode(true);
        var row = fragment.querySelector(".upgrade-third-party-row");
        var source = row.querySelector(".tp-source");
        var name = row.querySelector(".tp-name");
        var packageSelect = row.querySelector(".tp-package");
        source.addEventListener("change", function () {
            row.querySelector(".tp-git-fields").classList.toggle("d-none", source.value !== "git");
            row.querySelector(".tp-package-fields").classList.toggle("d-none", source.value !== "package");
        });
        packageSelect.addEventListener("change", function () {
            if (!name.value && packageSelect.selectedOptions[0]) {
                name.value = packageSelect.selectedOptions[0].dataset.name || "";
            }
        });
        row.querySelector(".tp-remove").addEventListener("click", function () { row.remove(); });
        document.getElementById("thirdPartyRows").appendChild(fragment);
    }

    function collectThirdParty() {
        var result = [];
        var errors = [];
        document.querySelectorAll(".upgrade-third-party-row").forEach(function (row) {
            var source = row.querySelector(".tp-source").value;
            var name = row.querySelector(".tp-name").value.trim();
            if (!name) return;
            if (source === "git") {
                var gitUrl = row.querySelector(".tp-git").value.trim();
                var branch = row.querySelector(".tp-branch").value.trim() || "master";
                if (!gitUrl) errors.push("模块 " + name + " 缺少 Git 仓库 URL");
                else result.push({ name: name, source: "git", git_url: gitUrl, branch: branch });
            } else {
                var packageId = row.querySelector(".tp-package").value;
                if (!packageId) errors.push("模块 " + name + " 需要选择离线包");
                else result.push({ name: name, source: "package", package_id: Number(packageId) });
            }
        });
        return { items: result, errors: errors };
    }

    function getConfigurePayload() {
        var ids = selectedNodeIds();
        return ids.map(function (id) {
            var baseline = window.upgradeBaselines[id].data;
            return {
                node_id: id,
                current_version: baseline.version,
                current_configure_opts: baseline.configure_opts,
                params: baseline.params,
                prefix: baseline.prefix,
                binary_path: baseline.binary_path
            };
        });
    }

    function previewUpgrade() {
        var thirdParty = collectThirdParty();
        if (thirdParty.errors.length) {
            notify(thirdParty.errors[0], "warning");
            return;
        }
        var selected = selectedNodes();
        var pkg = selectedPackage();
        if (!selected.length || !pkg) {
            notify("请选择目标节点和源码包", "warning");
            activateStep(1);
            return;
        }
        if (currentMode() === "switch_path" && !document.getElementById("targetPrefix").value.trim()) {
            notify("切换路径模式请填写目标安装目录", "warning");
            return;
        }
        var changes = collectModuleChanges();
        var first = window.upgradeBaselines[selected[0].id].data;
        var payload = {
            current_params: first.params,
            added_modules: changes.added,
            removed_modules: changes.removed,
            added_third_party: thirdParty.items,
            remote_work_dir: currentWorkDir(),
            upgrade_mode: currentMode(),
            target_prefix: document.getElementById("targetPrefix").value.trim()
        };
        requestJson("POST", "/api/upgrade/compute-config", payload)
            .done(function (data) {
                var opts = data.target_opts;
                document.getElementById("finalConfigPreview").textContent = "./configure \\\n    " + opts.replace(/\s+\\\n\s+/g, " \\\n    ");
                renderConfirm(selected, pkg, thirdParty.items, changes);
                activateStep(4);
            })
            .fail(function (error) { notify(errorMessage(error), "danger"); });
    }

    function renderConfirm(nodes, pkg, modules, changes) {
        var summary = document.getElementById("upgradeSummary");
        summary.replaceChildren();
        [
            ["节点", nodes.length + " 台"],
            ["源码包", pkg.name + " · " + pkg.version],
            ["模式", currentMode() === "upgrade" ? "平滑升级（同路径）" : "切换路径升级"],
            ["工作目录", currentWorkDir()],
            ["编译并行数", document.getElementById("makeJobs").value],
            ["模块变更", "新增 " + changes.added.length + " · 移除 " + changes.removed.length + " · 第三方 " + modules.length]
        ].forEach(function (pair) {
            var item = document.createElement("div");
            item.className = "upgrade-summary-item";
            var label = document.createElement("small");
            label.className = "text-muted d-block";
            label.textContent = pair[0];
            var value = document.createElement("strong");
            value.textContent = pair[1];
            item.append(label, value);
            summary.appendChild(item);
        });
        var nodeList = document.getElementById("confirmNodeList");
        nodeList.replaceChildren();
        nodes.forEach(function (node) {
            var baseline = window.upgradeBaselines[node.id].data;
            var row = document.createElement("div");
            row.className = "upgrade-confirm-item";
            var name = document.createElement("strong");
            name.textContent = node.name + " · " + node.ip;
            var versions = document.createElement("span");
            versions.className = "small text-muted";
            versions.textContent = (baseline.version || "未知") + " → " + pkg.version;
            row.append(name, versions);
            nodeList.appendChild(row);
        });
        document.getElementById("confirmUpgrade").checked = false;
        document.getElementById("startUpgrade").disabled = true;
    }

    function createUpgrade() {
        var pkg = selectedPackage();
        var changes = collectModuleChanges();
        var thirdParty = collectThirdParty();
        var payload = {
            node_ids: selectedNodeIds(),
            source_package: pkg.id,
            upgrade_mode: currentMode(),
            remote_work_dir: currentWorkDir(),
            make_jobs: Number(document.getElementById("makeJobs").value),
            target_version: pkg.version,
            target_prefix: document.getElementById("targetPrefix").value.trim(),
            added_modules: changes.added,
            removed_modules: changes.removed,
            added_third_party: thirdParty.items,
            nodes_payload: getConfigurePayload()
        };
        document.getElementById("startUpgrade").disabled = true;
        requestJson("POST", "/api/upgrade/tasks", payload)
            .done(function (result) {
                notify(result.message, "success");
                beginBatchPolling(result.task_ids, result.batch_number);
            })
            .fail(function (error) {
                document.getElementById("startUpgrade").disabled = false;
                notify(errorMessage(error), "danger");
            });
    }

    function beginBatchPolling(taskIds, batchNumber) {
        var region = document.getElementById("progressLive");
        region.classList.remove("d-none");
        document.getElementById("progressPlaceholder").classList.add("d-none");
        document.getElementById("batchProgressBadge").classList.remove("d-none");
        document.getElementById("batchNumberLabel").textContent = "批次 " + batchNumber;
        var states = {};
        taskIds.forEach(function (id) { states[id] = { after: 0, status: "pending", progress: 0, detail: "等待执行" }; });
        pollBatch(taskIds, states, batchNumber);
    }

    function pollBatch(taskIds, states, batchNumber) {
        var jobs = taskIds.map(function (id) {
            return requestJson("GET", "/api/tasks/" + id + "?after_log_id=" + states[id].after + "&log_limit=100")
                .done(function (task) {
                    states[id].status = task.status;
                    states[id].progress = task.progress;
                    states[id].detail = task.detail;
                    states[id].after = task.next_log_id;
                    states[id].task = task;
                });
        });
        $.when.apply($, jobs).always(function () {
            renderBatchTasks(taskIds, states, batchNumber);
            var done = taskIds.every(function (id) { return ["success", "failed", "cancelled"].indexOf(states[id].status) >= 0; });
            if (!done) window.setTimeout(function () { pollBatch(taskIds, states, batchNumber); }, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
        });
    }

    function renderBatchTasks(taskIds, states, batchNumber) {
        var rows = document.getElementById("batchTaskRows");
        rows.replaceChildren();
        var totalProgress = 0;
        taskIds.forEach(function (id) {
            var state = states[id];
            totalProgress += state.progress;
            var row = document.createElement("div");
            row.className = "upgrade-task-row";
            var summary = document.createElement("div");
            summary.className = "min-w-0";
            var label = document.createElement("strong");
            label.textContent = state.task ? state.task.target_hostnames + " · " + state.task.target_ips : "任务 #" + id;
            var step = document.createElement("small");
            step.className = "text-muted d-block text-truncate";
            step.textContent = state.detail;
            summary.append(label, step);
            var right = document.createElement("div");
            right.className = "text-end text-nowrap";
            var badge = document.createElement("span");
            badge.className = "badge text-bg-" + (state.status === "success" ? "success" : (state.status === "failed" ? "danger" : (state.status === "running" ? "info" : "secondary")));
            badge.textContent = ({ pending: "等待", running: "执行中", success: "完成", failed: "失败", cancelled: "已取消" })[state.status] || state.status;
            var link = document.createElement("a");
            link.className = "btn btn-outline-secondary btn-sm ms-1";
            link.href = "/upgrade/tasks/" + id + "/";
            link.title = "打开任务详情";
            link.setAttribute("aria-label", "打开任务详情");
            link.innerHTML = '<i class="bi bi-arrow-up-right" aria-hidden="true"></i>';
            right.append(badge, link);
            if (state.status === "pending" || state.status === "running") {
                var cancellable = state.status === "pending" || /加入队列|获取|检查|上传|创建远程|解压/.test(state.detail);
                if (cancellable) {
                    var cancel = document.createElement("button");
                    cancel.className = "btn btn-outline-danger btn-sm ms-1";
                    cancel.type = "button";
                    cancel.title = "取消升级";
                    cancel.setAttribute("aria-label", "取消升级");
                    cancel.innerHTML = '<i class="bi bi-stop-circle" aria-hidden="true"></i>';
                    cancel.addEventListener("click", function () { cancelUpgrade(id, batchNumber); });
                    right.appendChild(cancel);
                }
            }
            row.append(summary, right);
            rows.appendChild(row);
        });
        var average = taskIds.length ? Math.floor(totalProgress / taskIds.length) : 0;
        document.getElementById("batchProgressBar").style.width = average + "%";
        document.getElementById("batchProgressBadge").textContent = average + "%";
    }

    function cancelUpgrade(taskId, batchNumber) {
        window.showConfirm("取消升级", "确认取消该节点的升级任务？执行中的远程命令会在下一个检查点停止。", function () {
            requestJson("POST", "/api/upgrade/tasks/" + taskId + "/cancel", {})
                .done(function (result) { notify(result.message, "success"); })
                .fail(function (error) { notify(errorMessage(error), "danger"); });
        });
    }

    if (center) {
        window.upgradeBaselines = {};
        filterTable("nodeSearch", ".upgrade-node-row");
        filterTable("packageSearch", ".upgrade-package-row");
        document.querySelectorAll(".upgrade-node-check, .upgrade-package-check").forEach(function (input) {
            input.addEventListener("change", updateSelection);
        });
        updateSelection();
        document.getElementById("stepOneNext").addEventListener("click", function () { activateStep(2); });
        document.getElementById("stepTwoNext").addEventListener("click", function () {
            var jobs = Number(document.getElementById("makeJobs").value);
            if (!Number.isInteger(jobs) || jobs < 1 || jobs > 32) {
                notify("并行编译数必须在 1 到 32 之间", "warning");
                return;
            }
            if (currentMode() === "switch_path") {
                document.getElementById("targetPrefixWrap").classList.remove("d-none");
            }
            activateStep(3);
        });
        document.querySelectorAll("input[name='upgradeMode']").forEach(function (input) {
            input.addEventListener("change", function () {
                document.getElementById("targetPrefixWrap").classList.toggle("d-none", currentMode() !== "switch_path");
            });
        });
        document.getElementById("refreshNginxV").addEventListener("click", function () { fetchConfigureBaselines(true); });
        document.getElementById("moduleSearch").addEventListener("input", filterModuleList);
        document.getElementById("addThirdParty").addEventListener("click", addThirdPartyRow);
        document.getElementById("stepThreeNext").addEventListener("click", previewUpgrade);
        document.getElementById("confirmUpgrade").addEventListener("change", function () {
            var startButton = document.getElementById("startUpgrade");
            if (startButton) startButton.disabled = !this.checked;
        });
        var startButton = document.getElementById("startUpgrade");
        if (startButton) startButton.addEventListener("click", createUpgrade);
        document.querySelectorAll("[data-step-target]").forEach(function (button) {
            button.addEventListener("click", function () {
                var target = Number(button.dataset.stepTarget);
                if (target === 3) fetchConfigureBaselines(false);
                activateStep(target);
            });
        });
    }

    function appendLogs(task, log) {
        (task.logs || []).forEach(function (entry) {
            var span = document.createElement("span");
            span.className = "log-" + entry.level;
            span.textContent = entry.message + "\n";
            log.appendChild(span);
        });
        log.dataset.afterLogId = String(task.next_log_id || log.dataset.afterLogId || 0);
        log.scrollTop = log.scrollHeight;
    }

    function pollDetail() {
        var taskId = detail.dataset.taskId;
        var log = document.getElementById("upgradeLog");
        requestJson("GET", "/api/tasks/" + taskId + "?after_log_id=" + log.dataset.afterLogId + "&log_limit=200")
            .done(function (task) {
                appendLogs(task, log);
                document.getElementById("taskProgress").style.width = task.progress + "%";
                document.getElementById("taskProgressLabel").textContent = task.progress + "% · " + task.detail;
                document.getElementById("taskStatus").textContent = ({ pending: "等待执行", running: "执行中", success: "升级成功", failed: "升级失败", cancelled: "已取消" })[task.status] || task.status;
                var statusClass = task.status === "success" ? "success" : (task.status === "failed" ? "danger" : (task.status === "running" ? "info" : "secondary"));
                document.getElementById("taskStatus").className = "badge text-bg-" + statusClass;
                var cancelButton = document.getElementById("cancelUpgrade");
                var canCancelAtStep = task.status === "pending" || (task.status === "running" && /获取|检查 gcc|创建远程工作目录|上传 Nginx 源码包|解压 Nginx 源码包/.test(task.detail));
                if (cancelButton && !canCancelAtStep) {
                    cancelButton.remove();
                }
                if (["pending", "running"].indexOf(task.status) >= 0) {
                    window.setTimeout(pollDetail, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                } else {
                    document.getElementById("logLiveLabel").textContent = "已完成";
                }
            })
            .fail(function () { window.setTimeout(pollDetail, window.NGXOPS_TASK_POLL_INTERVAL || 2000); });
    }

    if (detail && ["pending", "running"].indexOf(detail.dataset.taskStatus) >= 0) {
        pollDetail();
    }
    if (detail) {
        var cancelButton = document.getElementById("cancelUpgrade");
        if (cancelButton) cancelButton.addEventListener("click", function () {
            window.showConfirm("取消升级", "确认取消当前任务？", function () {
                requestJson("POST", "/api/upgrade/tasks/" + detail.dataset.taskId + "/cancel", {})
                    .done(function (result) { notify(result.message, "success"); window.setTimeout(pollDetail, 400); })
                    .fail(function (error) { notify(errorMessage(error), "danger"); });
            });
        });
        var rollbackButton = document.getElementById("rollbackUpgrade");
        if (rollbackButton) rollbackButton.addEventListener("click", function () {
            window.showConfirm("回滚 Nginx", "确认恢复升级前的二进制并重新加载 Nginx？", function () {
                requestJson("POST", "/api/upgrade/tasks/" + detail.dataset.taskId + "/rollback", {})
                    .done(function (result) {
                        notify(result.message, "success");
                        window.location.assign("/upgrade/tasks/" + result.task_id + "/");
                    })
                    .fail(function (error) { notify(errorMessage(error), "danger"); });
            });
        });
    }
})(window.jQuery, window, document);
