(function () {
    "use strict";

    var center = document.getElementById("uninstallConfig");
    var detail = document.getElementById("uninstallTaskConfig");
    if (center) initCenter(center);
    if (detail) initTaskDetail(detail);

    function csrfToken() {
        var meta = document.querySelector("meta[name='csrf-token']");
        return meta ? meta.content : "";
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
        });
    }

    function requestJson(url, options) {
        return fetch(url, options || {}).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || data.success === false) {
                    throw new Error(data.message || data.detail || "请求失败");
                }
                return data;
            });
        });
    }

    function initCenter(config) {
        var maxCount = Number(config.dataset.maxCount || 3);
        var selected = Object.create(null);
        var previewNodes = [];
        var activeBatch = "";
        var pollTimer = 0;
        var searchTimer = 0;
        var canExecute = config.dataset.canExecute === "true";
        var statusLabels = { pending: "等待执行", running: "执行中", success: "卸载成功", failed: "卸载失败", cancelled: "已取消" };

        function loadNodes() {
            var search = document.getElementById("uninstallNodeSearch").value.trim();
            var body = document.getElementById("uninstallNodeRows");
            body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">正在加载节点</td></tr>';
            requestJson(config.dataset.nodesUrl + "?search=" + encodeURIComponent(search))
                .then(function (data) {
                    body.innerHTML = "";
                    data.nodes.forEach(function (node) {
                        var checked = selected[String(node.id)] ? " checked" : "";
                        var disabled = !canExecute || !node.can_select ? " disabled" : "";
                        var reason = node.disabled_reason ? '<span class="text-muted"> · ' + escapeHtml(node.disabled_reason) + "</span>" : "";
                        var groups = node.groups.length ? node.groups.map(escapeHtml).join("、") : "-";
                        var nginx = node.nginx_available === true ? '<span class="badge text-bg-success">可用</span>' : (node.nginx_available === false ? '<span class="badge text-bg-secondary">不可用</span>' : '<span class="badge text-bg-warning">未知</span>');
                        body.insertAdjacentHTML("beforeend", '<tr class="' + (disabled ? "text-muted" : "") + '"><td class="text-center"><input class="form-check-input uninstall-node-check" type="checkbox" value="' + node.id + '"' + checked + disabled + ' aria-label="选择 ' + escapeHtml(node.hostname) + '"></td><td><strong>' + escapeHtml(node.hostname) + '</strong><small class="d-block">' + escapeHtml(node.ip) + ':' + node.port + "</small>" + reason + '</td><td>' + groups + '</td><td>' + nginx + (node.nginx_version ? ' <code>' + escapeHtml(node.nginx_version) + "</code>" : "") + '</td><td><span class="badge text-bg-' + (node.status === "online" ? "success" : "secondary") + '">' + escapeHtml(node.status) + "</span></td></tr>");
                    });
                    if (!data.nodes.length) body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">没有匹配的节点</td></tr>';
                    updateSelectedCount();
                    updateSelectVisible();
                })
                .catch(function (error) {
                    body.innerHTML = '<tr><td colspan="5" class="text-center text-danger py-4">' + escapeHtml(error.message) + "</td></tr>";
                });
        }

        function updateSelectedCount() {
            var count = Object.keys(selected).length;
            document.getElementById("uninstallSelectedCount").textContent = "已选 " + count + " 台 / 上限 " + maxCount;
            document.getElementById("uninstallToStep2").disabled = !canExecute || count === 0 || count > maxCount;
        }

        function updateSelectVisible() {
            var checkboxes = Array.prototype.slice.call(document.querySelectorAll(".uninstall-node-check:not(:disabled)"));
            document.getElementById("uninstallSelectVisible").checked = checkboxes.length > 0 && checkboxes.every(function (item) { return item.checked; });
        }

        function setStep(step) {
            [1, 2, 3].forEach(function (number) {
                document.getElementById("uninstallStep" + number).classList.toggle("d-none", number !== step);
                var button = document.querySelector('[data-step-nav="' + number + '"]');
                button.classList.toggle("btn-primary", number === step);
                button.classList.toggle("btn-outline-secondary", number !== step);
                button.disabled = number > (previewNodes.length ? 3 : 1);
            });
        }

        function preview() {
            var ids = Object.keys(selected).map(Number);
            document.getElementById("uninstallPreviewNodes").innerHTML = '<div class="text-center text-muted py-4"><span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>正在通过 SSH 探测卸载范围</div>';
            requestJson(config.dataset.previewUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                body: JSON.stringify({ node_ids: ids })
            }).then(function (data) {
                previewNodes = data.nodes;
                renderPreview();
                setStep(2);
            }).catch(function (error) {
                window.showAlert("预览失败", error.message);
            });
        }

        function renderPreview() {
            var eligible = previewNodes.filter(function (node) { return node.eligible; });
            document.getElementById("uninstallPreviewSummary").textContent = eligible.length + " 台可执行，" + (previewNodes.length - eligible.length) + " 台未通过门禁";
            var root = document.getElementById("uninstallPreviewNodes");
            root.innerHTML = "";
            previewNodes.forEach(function (node) {
                var origin = node.install_origin === "package" ? "系统包" : (node.install_origin === "source" ? "源码安装" : "未确认");
                var mode = node.manage_mode === "systemctl" ? "systemctl · " + (node.manage_unit || "nginx") : "二进制启动";
                var summary = node.eligible ? (node.running ? '<span class="badge text-bg-warning">运行中</span>' : '<span class="badge text-bg-success">已停止</span>') : '<span class="badge text-bg-danger">' + escapeHtml(node.gate_message || "不可执行") + "</span>";
                var pathHtml = "";
                (node.paths || []).forEach(function (path, index) {
                    var checked = path.checked ? " checked" : "";
                    var disabled = path.required || path.package_owned || path.key === "prefix" ? " disabled" : "";
                    var input = path.key === "prefix" && path.editable
                        ? '<input class="form-control form-control-sm uninstall-prefix-input" value="' + escapeHtml(path.path) + '" data-node="' + node.id + '" aria-label="' + escapeHtml(node.hostname) + ' prefix">'
                        : '<input class="form-check-input uninstall-path-check" type="checkbox" data-node="' + node.id + '" data-path-index="' + index + '" data-key="' + escapeHtml(path.key) + '" data-path="' + escapeHtml(path.path) + '" data-kind="' + escapeHtml(path.kind) + '"' + checked + disabled + ' aria-label="选择删除路径 ' + escapeHtml(path.path) + '">';
                    var badge = path.package_owned ? '<span class="badge text-bg-info">包管理器处理</span>' : (path.required ? '<span class="badge text-bg-danger">必选</span>' : "");
                    if (["release_backup", "work_dir", "nginx_modules"].indexOf(path.key) >= 0) {
                        input = '<span class="badge text-bg-secondary">下方选项</span>';
                    }
                    pathHtml += '<div class="uninstall-path-row" data-node="' + node.id + '" data-path="' + escapeHtml(path.path) + '" data-kind="' + escapeHtml(path.kind) + '" data-required="' + (path.required ? "true" : "false") + '" data-package-owned="' + (path.package_owned ? "true" : "false") + '"><div class="form-check uninstall-path-toggle">' + (path.key === "prefix" ? '<span class="uninstall-path-required"><i class="bi bi-lock-fill" aria-hidden="true"></i></span>' : input) + '<span class="uninstall-path-label">' + escapeHtml(path.label) + "</span>" + badge + '</div><code class="uninstall-path-code">' + (path.key === "prefix" ? input : escapeHtml(path.path)) + '</code></div>';
                });
                var settings = node.install_origin === "package" ? '<p class="small text-muted mt-2 mb-2">软件包文件由包管理器管理，路径项不会通过 rm 删除。</p>' : "";
                var shallow = node.shallow_prefix ? '<div class="alert alert-warning py-2 small mt-2 mb-2">prefix 是一级目录，删除范围较大，可能包含 Nginx 以外的数据。</div>' : "";
                var pkg = node.install_origin === "package" ? '<span class="small">软件包：<code>' + escapeHtml(node.package_manager) + ":" + escapeHtml(node.package_name) + "</code></span>" : '<span class="small">prefix：<code>' + escapeHtml(node.prefix || "未解析") + "</code></span>";
                var warning = node.running_error ? '<div class="small text-warning mt-1">' + escapeHtml(node.running_error) + "</div>" : "";
                root.insertAdjacentHTML("beforeend", '<article class="uninstall-preview-node" data-node="' + node.id + '"><header class="d-flex justify-content-between align-items-start gap-2"><div><h3 class="h6 mb-1">' + escapeHtml(node.hostname) + ' <small class="text-muted">' + escapeHtml(node.ip) + '</small></h3><div class="d-flex flex-wrap gap-2 align-items-center">' + pkg + '<span class="small text-muted">托管：' + escapeHtml(mode) + "</span>" + summary + "</div>" + warning + '</div><button type="button" class="btn btn-sm btn-outline-secondary" data-bs-toggle="collapse" data-bs-target="#uninstallPaths' + node.id + '" aria-expanded="true" aria-label="折叠路径"><i class="bi bi-chevron-up" aria-hidden="true"></i></button></header>' + shallow + settings + '<div class="collapse show" id="uninstallPaths' + node.id + '"><div class="uninstall-path-list">' + pathHtml + "</div></div></article>");
            });
            root.insertAdjacentHTML("afterbegin", '<div class="row g-2 mb-3"><div class="col-12 col-lg-4"><div class="form-check"><input class="form-check-input" type="checkbox" id="removeBackup"><label class="form-check-label" for="removeBackup">同时清理发布备份</label><div class="small text-muted ms-4">' + escapeHtml(eligible[0] ? eligible[0].backup_path : "") + '</div></div></div><div class="col-12 col-lg-4"><div class="form-check"><input class="form-check-input" type="checkbox" id="removeWorkdir"><label class="form-check-label" for="removeWorkdir">同时清理编译工作目录</label></div></div><div class="col-12 col-lg-4"><div class="form-check"><input class="form-check-input" type="checkbox" id="removeModules"><label class="form-check-label" for="removeModules">同时清理第三方模块目录</label></div></div></div>');
            if (!eligible.length) {
                document.getElementById("uninstallToStep3").disabled = true;
            } else {
                document.getElementById("uninstallToStep3").disabled = !canExecute;
            }
            syncPathLocks();
        }

        function syncPathLocks() {
            document.querySelectorAll(".uninstall-preview-node").forEach(function (block) {
                var selectedRows = Array.prototype.slice.call(block.querySelectorAll(".uninstall-path-check:checked:not([data-inherited='true'])"));
                var prefixInput = block.querySelector(".uninstall-prefix-input");
                var prefixPath = prefixInput ? prefixInput.value.trim().replace(/\/$/, "") : "";
                block.querySelectorAll(".uninstall-path-check").forEach(function (checkbox) {
                    var path = checkbox.dataset.path;
                    var parent = selectedRows.some(function (parentBox) {
                        var parentPath = parentBox.dataset.path;
                        return parentBox.dataset.kind === "dir" && path !== parentPath && path.indexOf(parentPath.replace(/\/$/, "") + "/") === 0;
                    });
                    if (prefixPath && path.indexOf(prefixPath + "/") === 0) parent = true;
                    var row = checkbox.closest(".uninstall-path-row");
                    checkbox.disabled = row.dataset.required === "true" || row.dataset.packageOwned === "true" || parent;
                    if (parent && !checkbox.checked) {
                        checkbox.checked = true;
                        checkbox.dataset.inherited = "true";
                    }
                    if (!parent && checkbox.dataset.inherited === "true") {
                        checkbox.checked = false;
                        checkbox.dataset.inherited = "false";
                    }
                    row.classList.toggle("is-locked-by-parent", parent);
                    var badge = row.querySelector(".uninstall-path-parent-badge");
                    if (parent && !badge) row.querySelector(".uninstall-path-label").insertAdjacentHTML("afterend", '<span class="badge text-bg-secondary uninstall-path-parent-badge">随父路径</span>');
                    if (!parent && badge) badge.remove();
                });
            });
        }

        function selectedPayload() {
            return previewNodes.filter(function (node) { return node.eligible; }).map(function (node) {
                var prefixInput = document.querySelector('.uninstall-prefix-input[data-node="' + node.id + '"]');
                var extra = [];
                if (node.install_origin === "source") {
                    document.querySelectorAll('.uninstall-path-check[data-node="' + node.id + '"]:checked').forEach(function (checkbox) {
                        if (checkbox.dataset.key.indexOf("--") === 0 && !checkbox.disabled) {
                            extra.push({ key: checkbox.dataset.key, path: checkbox.dataset.path });
                        }
                    });
                }
                return {
                    id: node.id,
                    install_origin: node.install_origin,
                    package_manager: node.package_manager,
                    package_name: node.package_name,
                    prefix: prefixInput ? prefixInput.value.trim() : node.prefix,
                    remove_backup: document.getElementById("removeBackup").checked,
                    remove_workdir: document.getElementById("removeWorkdir").checked,
                    remove_modules: document.getElementById("removeModules").checked,
                    stop_if_running: true,
                    extra_paths: extra
                };
            });
        }

        function renderConfirmation() {
            var rows = selectedPayload().map(function (item) {
                var node = previewNodes.find(function (candidate) { return candidate.id === item.id; });
                var origin = item.install_origin === "package" ? "系统包 · " + item.package_manager + ":" + item.package_name : "源码 · " + item.prefix;
                var paths = item.install_origin === "package" ? ["包管理器卸载"] : [item.prefix].concat(item.extra_paths.map(function (entry) { return entry.path; }));
                if (item.remove_backup) paths.push(node.backup_path);
                if (item.remove_workdir) paths.push(node.work_dir);
                if (item.remove_modules) paths.push(node.modules_dir);
                return '<tr><td><strong>' + escapeHtml(node.hostname) + '</strong><small class="d-block text-muted">' + escapeHtml(node.ip) + '</small></td><td>' + escapeHtml(origin) + '</td><td><ul class="mb-0 ps-3">' + paths.map(function (path) { return "<li><code>" + escapeHtml(path) + "</code></li>"; }).join("") + "</ul></td></tr>";
            }).join("");
            document.getElementById("uninstallConfirmSummary").innerHTML = '<div class="table-responsive"><table class="table table-sm align-middle"><thead class="table-light"><tr><th>节点</th><th>卸载方式</th><th>远程删除范围</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
            document.getElementById("uninstallConfirm").checked = false;
            document.getElementById("uninstallStart").disabled = true;
        }

        function createBatch() {
            requestJson(config.dataset.createUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                body: JSON.stringify({ nodes: selectedPayload() })
            }).then(function (data) {
                activeBatch = data.batch_number;
                document.getElementById("uninstallProgressPlaceholder").classList.add("d-none");
                document.getElementById("uninstallProgress").classList.remove("d-none");
                document.getElementById("uninstallBatchLabel").textContent = "批次 " + data.batch_number;
                document.getElementById("uninstallCancel").classList.remove("d-none");
                window.showToast(data.message, "success");
                pollBatch();
            }).catch(function (error) { window.showAlert("创建失败", error.message); });
        }

        function start() {
            if (!document.getElementById("uninstallConfirm").checked) {
                window.showAlert("需要确认", "请先勾选卸载确认项。");
                return;
            }
            var active = selectedPayload();
            var running = previewNodes.filter(function (node) { return node.eligible && node.running; });
            var packages = previewNodes.filter(function (node) { return node.eligible && node.install_origin === "package"; });
            var systemRisk = previewNodes.filter(function (node) { return node.eligible && node.manage_mode === "systemctl" && node.credential_username !== "root"; });
            var notes = [];
            if (running.length) notes.push("运行中的 Nginx 将先停止：" + running.map(function (node) { return node.hostname; }).join("、"));
            if (packages.length) notes.push("系统包将通过 remove 卸载，不会直接 rm 包管理文件：" + packages.map(function (node) { return node.hostname; }).join("、"));
            if (systemRisk.length) notes.push("非 root 账号将操作 systemd，请确认已配置免密 sudo：" + systemRisk.map(function (node) { return node.hostname + " (" + node.credential_username + ")"; }).join("、"));
            if (notes.length) {
                window.showConfirm("卸载前确认", "<ul class='mb-0'>" + notes.map(function (note) { return "<li>" + escapeHtml(note) + "</li>"; }).join("") + "</ul>", createBatch, true, "md", "继续卸载");
            } else {
                createBatch();
            }
        }

        function pollBatch() {
            if (!activeBatch) return;
            requestJson(config.dataset.batchUrlPrefix + encodeURIComponent(activeBatch)).then(function (data) {
                var rows = data.tasks.map(function (task) {
                    var statusClass = task.status === "success" ? "success" : (task.status === "failed" ? "danger" : (task.status === "running" ? "info" : "secondary"));
                    return '<tr><td><strong>' + escapeHtml(task.hostname) + '</strong><small class="d-block text-muted">' + escapeHtml(task.ip) + '</small></td><td>' + escapeHtml(task.detail || "-") + '</td><td><span class="badge text-bg-' + statusClass + '">' + escapeHtml(statusLabels[task.status] || task.status) + '</span></td><td>' + task.progress + '%</td><td><a class="btn btn-outline-primary btn-sm" href="/nginx/uninstall/task/' + task.task_id + '/log/" aria-label="查看完整日志" title="查看完整日志"><i class="bi bi-file-text" aria-hidden="true"></i></a></td></tr>';
                }).join("");
                document.getElementById("uninstallProgressRows").innerHTML = rows;
                document.getElementById("uninstallProgressBar").style.width = data.progress + "%";
                document.getElementById("uninstallProgressPercent").textContent = data.progress + "%";
                document.getElementById("uninstallProgressCounts").textContent = "完成 " + data.tasks.filter(function (task) { return task.finished; }).length + " / " + data.total + " · 成功 " + data.success_count + " · 失败 " + data.fail_count;
                document.getElementById("uninstallCancel").classList.toggle("d-none", data.finished);
                if (!data.finished) pollTimer = window.setTimeout(pollBatch, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                else {
                    document.getElementById("uninstallProgressBar").classList.remove("progress-bar-animated");
                    window.showToast("卸载批次已完成", data.fail_count ? "warning" : "success");
                }
            }).catch(function (error) {
                document.getElementById("uninstallProgressCounts").textContent = error.message;
                pollTimer = window.setTimeout(pollBatch, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            });
        }

        document.getElementById("uninstallNodeRefresh").addEventListener("click", loadNodes);
        document.getElementById("uninstallNodeSearch").addEventListener("input", function () {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(loadNodes, 250);
        });
        document.getElementById("uninstallNodeSearch").addEventListener("keydown", function (event) {
            if (event.key === "Enter") { event.preventDefault(); loadNodes(); }
        });
        document.getElementById("uninstallNodeRows").addEventListener("change", function (event) {
            if (!event.target.matches(".uninstall-node-check")) return;
            var id = String(event.target.value);
            if (event.target.checked && !selected[id] && Object.keys(selected).length >= maxCount) {
                event.target.checked = false;
                window.showAlert("已达到上限", "单次最多选择 " + maxCount + " 台节点。");
                return;
            }
            if (event.target.checked) selected[id] = true;
            else delete selected[id];
            updateSelectedCount();
            updateSelectVisible();
        });
        document.getElementById("uninstallSelectVisible").addEventListener("change", function (event) {
            var checks = Array.prototype.slice.call(document.querySelectorAll(".uninstall-node-check:not(:disabled)"));
            if (!event.target.checked) {
                checks.forEach(function (item) { item.checked = false; delete selected[String(item.value)]; });
            } else {
                checks.some(function (item) {
                    if (Object.keys(selected).length >= maxCount) return true;
                    item.checked = true;
                    selected[String(item.value)] = true;
                    return false;
                });
            }
            updateSelectedCount();
            updateSelectVisible();
        });
        document.getElementById("uninstallToStep2").addEventListener("click", preview);
        document.getElementById("uninstallBackToStep1").addEventListener("click", function () { setStep(1); });
        document.getElementById("uninstallBackToStep2").addEventListener("click", function () { setStep(2); });
        document.getElementById("uninstallToStep3").addEventListener("click", function () {
            var eligible = previewNodes.some(function (node) { return node.eligible; });
            var badPrefix = selectedPayload().some(function (item) { return item.install_origin === "source" && !item.prefix; });
            if (!eligible || badPrefix) { window.showAlert("路径无效", "请检查 prefix 和节点预览结果。"); return; }
            renderConfirmation();
            setStep(3);
        });
        document.getElementById("uninstallPreviewNodes").addEventListener("change", function (event) {
            if (event.target.matches(".uninstall-path-check")) syncPathLocks();
            if (event.target.matches(".uninstall-prefix-input")) syncPathLocks();
        });
        document.getElementById("uninstallConfirm").addEventListener("change", function (event) {
            document.getElementById("uninstallStart").disabled = !event.target.checked || !canExecute;
        });
        document.getElementById("uninstallStart").addEventListener("click", start);
        document.getElementById("uninstallCancel").addEventListener("click", cancelBatch);
        document.querySelectorAll("[data-step-nav]").forEach(function (button) {
            button.addEventListener("click", function () {
                if (!button.disabled) setStep(Number(button.dataset.stepNav));
            });
        });

        function cancelBatch() {
            if (!activeBatch) return;
            requestJson(config.dataset.batchUrlPrefix + encodeURIComponent(activeBatch)).then(function (data) {
                return Promise.all(data.tasks.filter(function (task) { return !task.finished; }).map(function (task) {
                    return requestJson(config.dataset.cancelUrlPrefix + task.task_id + "/cancel", {
                        method: "POST",
                        headers: { "X-CSRFToken": csrfToken() }
                    }).catch(function () { return null; });
                }));
            }).then(function () { window.showToast("已发送协作取消请求", "info"); pollBatch(); })
                .catch(function (error) { window.showAlert("取消失败", error.message); });
        }

        setStep(1);
        loadNodes();
    }

    function initTaskDetail(config) {
        var taskId = config.dataset.taskId;
        var cursor = Number(config.dataset.logCursor || 0);
        var canCancel = config.dataset.canCancel === "true";
        var statusLabels = { pending: "等待执行", running: "执行中", success: "卸载成功", failed: "卸载失败", cancelled: "已取消" };

        function poll() {
            requestJson(config.dataset.taskUrlPrefix + taskId + "?after_log_id=" + cursor).then(function (data) {
                document.getElementById("uninstallTaskStatus").textContent = statusLabels[data.status] || data.status;
                document.getElementById("uninstallTaskProgress").textContent = data.progress + "%";
                document.getElementById("uninstallTaskDetail").textContent = data.detail;
                document.getElementById("uninstallLogState").textContent = data.status === "pending" || data.status === "running" ? "实时更新中" : "执行完成";
                var logs = document.getElementById("uninstallTaskLogs");
                data.logs.forEach(function (entry) {
                    logs.textContent += "[" + (entry.created_at ? entry.created_at.slice(11, 19) : "") + "] " + entry.message + "\n";
                    cursor = Math.max(cursor, entry.id);
                });
                logs.scrollTop = logs.scrollHeight;
                if (data.result_tree) document.getElementById("uninstallTaskResult").textContent = JSON.stringify(data.result_tree, null, 2);
                if (data.status === "pending" || data.status === "running") window.setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                else {
                    var cancel = document.getElementById("uninstallTaskCancel");
                    if (cancel) cancel.remove();
                }
            }).catch(function () { window.setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000); });
        }

        var cancelButton = document.getElementById("uninstallTaskCancel");
        if (cancelButton && canCancel) {
            cancelButton.addEventListener("click", function () {
                window.showConfirm("取消卸载", "已完成的远程删除不能回滚。确认发送协作取消请求？", function () {
                    requestJson(config.dataset.cancelUrlPrefix + taskId + "/cancel", {
                        method: "POST",
                        headers: { "X-CSRFToken": csrfToken() }
                    }).then(function (data) { window.showToast(data.message || "已发送取消请求", "info"); })
                        .catch(function (error) { window.showAlert("取消失败", error.message); });
                }, false, "md", "确认取消");
            });
        }
        if (document.getElementById("uninstallTaskStatus").textContent === "等待执行" || document.getElementById("uninstallTaskStatus").textContent === "执行中") poll();
    }
})();
