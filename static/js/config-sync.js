/* 配置发现清单和真实任务轮询交互。 */
(function () {
    "use strict";

    var page = document.getElementById("configSyncPage");
    if (!page) return;

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.content : "";
    var maxBatch = Number(page.dataset.maxBatch || 3);
    var selectAll = document.getElementById("selectSyncNodes");
    var batchButton = document.getElementById("batchConfigSync");
    var selectionCount = document.getElementById("syncSelectionCount");

    function requestJson(url, options) {
        var requestOptions = options || {};
        requestOptions.credentials = "same-origin";
        requestOptions.headers = Object.assign({}, requestOptions.headers || {}, {
            Accept: "application/json"
        });
        return fetch(url, requestOptions).then(function (response) {
            return response.json().then(function (payload) {
                if (!response.ok) throw new Error(payload.message || "请求失败");
                return payload;
            });
        });
    }

    function postJson(url, payload) {
        return requestJson(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken
            },
            body: JSON.stringify(payload)
        });
    }

    function delay(milliseconds) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, milliseconds);
        });
    }

    function pollTask(taskUrl, onUpdate) {
        return requestJson(taskUrl).then(function (task) {
            onUpdate(task);
            if (["success", "failed", "cancelled"].indexOf(task.status) >= 0) {
                return task;
            }
            return delay(window.NGXOPS_TASK_POLL_INTERVAL || 2000).then(function () {
                return pollTask(taskUrl, onUpdate);
            });
        });
    }

    function readAllTaskLogs(taskUrl, task) {
        var logs = (task.logs || []).slice();
        var nextLogId = task.next_log_id || 0;

        function readNextPage() {
            var query = "?after_log_id=" + encodeURIComponent(nextLogId) + "&log_limit=500";
            return requestJson(taskUrl + query).then(function (page) {
                logs = logs.concat(page.logs || []);
                nextLogId = page.next_log_id || nextLogId;
                if (page.has_more_logs) return readNextPage();
                return logs;
            });
        }

        return task.has_more_logs ? readNextPage() : Promise.resolve(logs);
    }

    function setStatus(element, message, kind) {
        if (!element) return;
        element.textContent = message || "";
        element.className = "small " + (kind || "text-muted");
    }

    function appendPathError(container, error) {
        var line = document.createElement("div");
        line.className = "text-danger";
        line.textContent = (error.path ? error.path + "：" : "") + error.message;
        container.appendChild(line);
    }

    function renderDiscoveredFiles(modal, files) {
        var container = modal.querySelector(".config-discovery-files");
        var partialButton = modal.querySelector(".partial-config-sync");
        var selectAllFiles = modal.querySelector(".select-discovered-files");
        container.replaceChildren();
        selectAllFiles.checked = false;
        selectAllFiles.indeterminate = false;
        selectAllFiles.disabled = files.length === 0;
        if (!files.length) {
            var empty = document.createElement("div");
            empty.className = "small text-muted";
            empty.textContent = "未发现配置文件";
            container.appendChild(empty);
            partialButton.disabled = true;
            return;
        }
        files.forEach(function (file, index) {
            var row = document.createElement("label");
            row.className = "form-check config-sync-file border-bottom py-2";
            var checkbox = document.createElement("input");
            checkbox.className = "form-check-input discovered-config-path";
            checkbox.type = "checkbox";
            checkbox.value = file.path;
            checkbox.id = "discoveredPath" + modal.dataset.nodeId + "_" + index;
            var path = document.createElement("span");
            path.className = "form-check-label small font-monospace text-break";
            path.textContent = file.path;
            row.appendChild(checkbox);
            row.appendChild(path);
            container.appendChild(row);
        });
        partialButton.disabled = true;
    }

    function renderTaskResult(element, task, logs) {
        var tree = task.result_tree || {};
        var lines = ["任务 #" + task.id + " · " + task.status + " · " + task.detail];
        (tree.nodes || []).forEach(function (node) {
            var counts = node.counts || {};
            ["created", "updated", "skipped", "orphaned", "deleted"].forEach(function (key) {
                (node[key] || []).forEach(function (item) {
                    lines.push(key + " · " + (item.path || item.name || ""));
                });
            });
            if (node.omitted_detail_count) lines.push("其余操作明细 " + node.omitted_detail_count + " 项见任务日志");
            if (node.omitted_error_count) lines.push("另有 " + node.omitted_error_count + " 项错误摘要见任务日志");
            (node.errors || []).forEach(function (error) {
                lines.push("失败 · " + (error.path ? error.path + "：" : "") + error.message);
            });
            if (Object.keys(counts).length) {
                lines.push("完整计数 · 新增 " + (counts.created || 0) + "，更新 " + (counts.updated || 0) + "，跳过 " + (counts.skipped || 0));
            }
        });
        (logs || []).forEach(function (log) {
            lines.push(log.level + " · " + log.message);
        });
        element.textContent = lines.join("\n");
        element.classList.remove("d-none");
    }

    function selectedNodeIds() {
        return Array.prototype.slice.call(
            document.querySelectorAll(".config-sync-node:checked")
        ).map(function (checkbox) {
            return Number(checkbox.value);
        });
    }

    function updateSelection() {
        var boxes = document.querySelectorAll(".config-sync-node");
        var selected = selectedNodeIds();
        boxes.forEach(function (checkbox) {
            checkbox.disabled = checkbox.dataset.eligible !== "true" || (!checkbox.checked && selected.length >= maxBatch);
        });
        if (selectionCount) {
            selectionCount.textContent = selected.length ? "已选择 " + selected.length + " / " + maxBatch + " 个节点" : "未选择节点";
        }
        if (batchButton) batchButton.disabled = selected.length === 0 || selected.length > maxBatch;
        if (selectAll) {
            var eligible = Array.prototype.filter.call(boxes, function (checkbox) {
                return checkbox.dataset.eligible === "true";
            });
            selectAll.checked = eligible.length > 0 && eligible.every(function (checkbox) {
                return checkbox.checked;
            });
            selectAll.indeterminate = selected.length > 0 && !selectAll.checked;
        }
    }

    function startDiscovery(modal) {
        var path = modal.querySelector(".main-conf-path").value.trim();
        var progress = modal.querySelector(".discovery-progress");
        var errors = modal.querySelector(".discovery-errors");
        var button = modal.querySelector(".discover-configs");
        if (!path) {
            setStatus(progress, "请输入 Nginx 主配置路径", "text-danger");
            return;
        }
        button.disabled = true;
        errors.replaceChildren();
        renderDiscoveredFiles(modal, []);
        setStatus(progress, "正在创建发现任务…");
        postJson("/api/configs/discover", {
            node_id: Number(modal.dataset.nodeId),
            main_conf_path: path
        }).then(function (created) {
            return pollTask(created.task_url, function (task) {
                setStatus(progress, task.progress + "% · " + task.detail);
            });
        }).then(function (task) {
            return readAllTaskLogs("/api/tasks/" + task.id, task).then(function (logs) {
                var result = task.result_tree && task.result_tree.nodes && task.result_tree.nodes[0] || {};
                var filesByPath = new Map();
                (result.files || []).forEach(function (file) {
                    filesByPath.set(file.path, {path: file.path});
                });
                logs.forEach(function (log) {
                    if (log.message.indexOf("发现配置 ") === 0) {
                        var path = log.message.slice("发现配置 ".length);
                        filesByPath.set(path, {path: path});
                    }
                });
                renderDiscoveredFiles(modal, Array.from(filesByPath.values()));
                (result.errors || []).forEach(function (error) {
                    appendPathError(errors, error);
                });
                var failed = (result.errors || []).length > 0;
                var foundCount = (result.summary && result.summary.success) || filesByPath.size;
                var omitted = result.omitted_file_count || 0;
                setStatus(
                    progress,
                    "发现 " + foundCount + " 个配置文件" +
                        (omitted ? "，路径已从任务日志补全" : ""),
                    failed ? "text-warning" : "text-success"
                );
            });
        }).catch(function (error) {
            setStatus(progress, error.message, "text-danger");
        }).finally(function () {
            button.disabled = false;
        });
    }

    function startSingleSync(modal, mode) {
        var path = modal.querySelector(".main-conf-path").value.trim();
        var selected = Array.prototype.slice.call(
            modal.querySelectorAll(".discovered-config-path:checked")
        ).map(function (checkbox) {
            return checkbox.value;
        });
        var status = modal.querySelector(".sync-task-status");
        var result = modal.querySelector(".sync-task-result");
        if (mode === "partial" && selected.length === 0) {
            setStatus(status, "请至少勾选一个配置文件", "text-danger");
            return;
        }
        modal.querySelector(".full-config-sync").disabled = true;
        modal.querySelector(".partial-config-sync").disabled = true;
        setStatus(status, "正在创建同步任务…");
        result.classList.add("d-none");
        postJson("/api/configs/sync", {
            node_id: Number(modal.dataset.nodeId),
            main_conf_path: path,
            mode: mode,
            selected_paths: mode === "partial" ? selected : []
        }).then(function (created) {
            return pollTask(created.task_url, function (task) {
                setStatus(status, task.progress + "% · " + task.detail);
            });
        }).then(function (task) {
            return readAllTaskLogs("/api/tasks/" + task.id, task).then(function (logs) {
                renderTaskResult(result, task, logs);
                setStatus(status, "同步任务结束", task.status === "success" ? "text-success" : "text-warning");
            });
        }).catch(function (error) {
            setStatus(status, error.message, "text-danger");
        }).finally(function () {
            modal.querySelector(".full-config-sync").disabled = false;
            modal.querySelector(".partial-config-sync").disabled =
                modal.querySelectorAll(".discovered-config-path:checked").length === 0;
        });
    }

    function startBatchSync() {
        var ids = selectedNodeIds();
        var status = document.getElementById("batchSyncStatus");
        if (ids.length === 0) {
            setStatus(status, "请选择需要同步的节点", "text-warning");
            return;
        }
        if (ids.length > maxBatch) {
            setStatus(status, "最多选择 " + maxBatch + " 个节点", "text-danger");
            return;
        }
        window.showConfirm(
            "确认批量配置同步",
            "确定要对选中的 " + ids.length + " 个节点进行全量配置同步吗？已标记删除的远程文件会在完整扫描后清理。",
            function () {
                batchButton.disabled = true;
                setStatus(status, "正在创建批量同步任务…");
                postJson("/api/configs/sync/batch", {node_ids: ids}).then(function (created) {
                    return pollTask(created.task_url, function (task) {
                        setStatus(status, "任务 #" + task.id + " · " + task.progress + "% · " + task.detail);
                    });
                }).then(function (task) {
                    var tree = task.result_tree || {};
                    var details = (tree.nodes || []).map(function (node) {
                        var counts = node.counts || {};
                        return node.hostname + "：新增 " + (counts.created || (node.created || []).length) +
                            "，更新 " + (counts.updated || (node.updated || []).length) +
                            "，失败 " + (node.errors || []).length;
                    });
                    setStatus(status, "任务 #" + task.id + " · " + task.detail + (details.length ? " · " + details.join("；") : ""), task.status === "success" ? "text-success" : "text-warning");
                }).catch(function (error) {
                    setStatus(status, error.message, "text-danger");
                }).finally(function () {
                    batchButton.disabled = selectedNodeIds().length === 0 || selectedNodeIds().length > maxBatch;
                });
            });
    }

    if (selectAll) {
        selectAll.addEventListener("change", function () {
            var selected = selectedNodeIds().length;
            var boxes = document.querySelectorAll(".config-sync-node");
            if (!selectAll.checked || selected >= maxBatch) {
                boxes.forEach(function (checkbox) { checkbox.checked = false; });
            } else {
                var remaining = maxBatch - selected;
                boxes.forEach(function (checkbox) {
                    if (checkbox.dataset.eligible === "true" && !checkbox.checked && remaining > 0) {
                        checkbox.checked = true;
                        remaining -= 1;
                    }
                });
            }
            updateSelection();
        });
    }
    document.querySelectorAll(".config-sync-node").forEach(function (checkbox) {
        checkbox.addEventListener("change", updateSelection);
    });
    document.querySelectorAll("tr[data-sync-selectable='true']").forEach(function (row) {
        row.addEventListener("click", function (event) {
            if (event.target.closest("a, button, input, label, select, textarea")) return;
            var checkbox = row.querySelector(".config-sync-node");
            if (!checkbox || checkbox.disabled) return;
            checkbox.checked = !checkbox.checked;
            updateSelection();
        });
        row.addEventListener("keydown", function (event) {
            if (event.target !== row || (event.key !== "Enter" && event.key !== " ")) return;
            event.preventDefault();
            var checkbox = row.querySelector(".config-sync-node");
            if (!checkbox || checkbox.disabled) return;
            checkbox.checked = !checkbox.checked;
            updateSelection();
        });
    });
    if (batchButton) batchButton.addEventListener("click", startBatchSync);

    document.querySelectorAll(".modal[data-node-id]").forEach(function (modal) {
        modal.querySelector(".discover-configs").addEventListener("click", function () {
            startDiscovery(modal);
        });
        modal.querySelector(".config-discovery-files").addEventListener("change", function (event) {
            if (event.target.classList.contains("discovered-config-path")) {
                var allFiles = modal.querySelectorAll(".discovered-config-path");
                var selectedFiles = modal.querySelectorAll(".discovered-config-path:checked");
                modal.querySelector(".partial-config-sync").disabled = selectedFiles.length === 0;
                var selectAllFiles = modal.querySelector(".select-discovered-files");
                selectAllFiles.checked = allFiles.length > 0 && selectedFiles.length === allFiles.length;
                selectAllFiles.indeterminate = selectedFiles.length > 0 && selectedFiles.length < allFiles.length;
            }
        });
        modal.querySelector(".select-discovered-files").addEventListener("change", function () {
            modal.querySelectorAll(".discovered-config-path").forEach(function (checkbox) {
                checkbox.checked = this.checked;
            }, this);
            modal.querySelector(".partial-config-sync").disabled = !this.checked;
        });
        modal.querySelector(".full-config-sync").addEventListener("click", function () {
            startSingleSync(modal, "full");
        });
        modal.querySelector(".partial-config-sync").addEventListener("click", function () {
            startSingleSync(modal, "partial");
        });
    });
    updateSelection();
})();
