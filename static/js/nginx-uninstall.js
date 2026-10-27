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
        var previewRequestId = 0;
        var acknowledgedForeignPaths = Object.create(null);
        var activeBatch = "";
        var pollTimer = 0;
        var canExecute = config.dataset.canExecute === "true";
        var statusLabels = { pending: "等待执行", running: "执行中", success: "卸载成功", failed: "卸载失败", cancelled: "已取消" };
        var nodeRows = Array.prototype.slice.call(document.querySelectorAll(".uninstall-node-row"));
        var nodeSearch = document.getElementById("uninstallNodeSearch");
        var nodePageSize = document.getElementById("uninstallNodePageSize");
        var nodePage = 1;

        function foreignPathsDetails(paths, summary) {
            var items = paths.map(function (path) {
                return '<li><code>' + escapeHtml(path) + '</code></li>';
            }).join("");
            return '<details class="uninstall-foreign-path-details"><summary>' + summary + '</summary><ul class="uninstall-foreign-path-list">' + items + "</ul></details>";
        }

        function selectedNodeIds() {
            return Object.keys(selected);
        }

        function renderSelectionSummary() {
            var summary = document.getElementById("selectedNodes");
            var empty = document.getElementById("emptySelection");
            var ids = selectedNodeIds();
            summary.replaceChildren();
            empty.classList.toggle("d-none", ids.length > 0);
            ids.forEach(function (id) {
                var item = selected[id];
                var chip = document.createElement("span");
                chip.className = "upgrade-selected-chip";
                var label = document.createElement("span");
                label.textContent = item.name + " (" + item.ip + ")";
                var remove = document.createElement("button");
                remove.type = "button";
                remove.className = "upgrade-chip-remove";
                remove.title = "移除节点";
                remove.setAttribute("aria-label", "移除节点 " + item.name);
                remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
                remove.addEventListener("click", function () {
                    var checkbox = document.querySelector(".uninstall-node-check[value='" + id + "']");
                    if (checkbox) checkbox.checked = false;
                    delete selected[id];
                    updateSelection();
                    renderNodePage();
                });
                chip.append(label, remove);
                summary.appendChild(chip);
            });
        }

        function updateSelection() {
            var count = selectedNodeIds().length;
            document.querySelectorAll(".uninstall-node-check").forEach(function (checkbox) {
                var isSelected = Boolean(selected[String(checkbox.value)]);
                checkbox.checked = isSelected;
                checkbox.disabled = !canExecute || (!isSelected && count >= maxCount);
            });
            document.getElementById("uninstallToStep2").disabled = !canExecute || count === 0 || count > maxCount;
            renderSelectionSummary();
        }

        function matchingNodeRows() {
            var terms = window.getQueryTagValue("#uninstallNodeSearch")
                .split(/[,，]/).map(function (term) { return term.trim().toLocaleLowerCase(); })
                .filter(Boolean);
            return nodeRows.filter(function (row) {
                var text = (row.dataset.search || "").toLocaleLowerCase();
                return terms.every(function (term) { return text.includes(term); });
            });
        }

        function renderNodePage() {
            var matching = matchingNodeRows();
            var size = Number(nodePageSize.value) || 10;
            var pages = Math.max(1, Math.ceil(matching.length / size));
            nodePage = Math.min(nodePage, pages);
            var start = (nodePage - 1) * size;
            nodeRows.forEach(function (row) { row.classList.add("d-none"); });
            matching.slice(start, start + size).forEach(function (row) { row.classList.remove("d-none"); });
            var noResults = document.getElementById("uninstallNodeNoResults");
            if (noResults) noResults.classList.toggle("d-none", matching.length > 0);
            document.getElementById("uninstallNodeResultCount").textContent = "共 " + matching.length + " 台";
            document.getElementById("uninstallNodePageLabel").textContent = nodePage + " / " + pages;
            document.getElementById("uninstallNodePageFirst").disabled = nodePage <= 1;
            document.getElementById("uninstallNodePagePrevious").disabled = nodePage <= 1;
            document.getElementById("uninstallNodePageNext").disabled = nodePage >= pages;
            document.getElementById("uninstallNodePageLast").disabled = nodePage >= pages;
            updateSelection();
        }

        function bindNodePicker() {
            var first = document.getElementById("uninstallNodePageFirst");
            var previous = document.getElementById("uninstallNodePagePrevious");
            var next = document.getElementById("uninstallNodePageNext");
            var last = document.getElementById("uninstallNodePageLast");
            nodeSearch.addEventListener("keydown", function (event) {
                if (event.key !== "Enter") return;
                event.preventDefault();
                window.commitQueryTagValue("#uninstallNodeSearch");
                nodePage = 1;
                renderNodePage();
            });
            $(nodeSearch).on("querytags:change", function () {
                nodePage = 1;
                renderNodePage();
            });
            nodePageSize.addEventListener("change", function () { nodePage = 1; renderNodePage(); });
            first.addEventListener("click", function () { nodePage = 1; renderNodePage(); });
            previous.addEventListener("click", function () { nodePage = Math.max(1, nodePage - 1); renderNodePage(); });
            next.addEventListener("click", function () { nodePage += 1; renderNodePage(); });
            last.addEventListener("click", function () {
                nodePage = Math.max(1, Math.ceil(matchingNodeRows().length / (Number(nodePageSize.value) || 10)));
                renderNodePage();
            });
            document.getElementById("uninstallNodeRows").addEventListener("change", function (event) {
                if (!event.target.matches(".uninstall-node-check")) return;
                var row = event.target.closest(".uninstall-node-row");
                var id = String(event.target.value);
                if (event.target.checked && !selected[id] && selectedNodeIds().length >= maxCount) {
                    event.target.checked = false;
                    window.showAlert("已达到上限", "单次最多选择 " + maxCount + " 台节点。");
                    return;
                }
                if (event.target.checked) {
                    selected[id] = { name: row.dataset.name, ip: row.dataset.ip };
                } else {
                    delete selected[id];
                }
                updateSelection();
            });
            nodeRows.forEach(function (row) {
                row.addEventListener("click", function (event) {
                    if (event.target.closest("input, button, a, label, select")) return;
                    var checkbox = row.querySelector(".uninstall-node-check");
                    if (!checkbox || checkbox.disabled) return;
                    checkbox.checked = !checkbox.checked;
                    checkbox.dispatchEvent(new Event("change", { bubbles: true }));
                });
            });
            renderNodePage();
        }

        function setStep(step) {
            [1, 2, 3].forEach(function (number) {
                document.getElementById("uninstallStep" + number).classList.toggle("d-none", number !== step);
            });
        }

        function renderPreviewLoading(ids) {
            var root = document.getElementById("uninstallPreviewNodes");
            var items = ids.map(function (id) {
                var node = selected[String(id)] || { name: "节点 " + id, ip: "" };
                return '<div class="uninstall-preview-wait-item" id="uninstallPreviewWait' + id + '"><div class="d-flex justify-content-between align-items-center gap-2"><strong>' + escapeHtml(node.name) + ' (' + escapeHtml(node.ip) + ')</strong><span class="uninstall-preview-wait-status uninstall-preview-pending">正在获取删除路径</span></div><div class="small text-muted mt-1">读取 Nginx 编译参数、安装来源和目录内容</div></div>';
            }).join("");
            root.innerHTML = '<div class="uninstall-preview-loading" aria-live="polite"><div class="d-flex justify-content-between align-items-center small mb-2"><strong>正在获取删除路径</strong><span id="uninstallPreviewWaitCount">0 / ' + ids.length + ' 台完成</span></div><div class="progress mb-2" style="height:6px" role="progressbar" aria-label="卸载范围获取进度" aria-valuemin="0" aria-valuemax="' + ids.length + '" aria-valuenow="0"><div class="progress-bar progress-bar-striped progress-bar-animated" id="uninstallPreviewWaitBar" style="width:0%"></div></div><div class="uninstall-preview-wait-list">' + items + '</div></div>';
        }

        function updatePreviewLoading(nodeId, node, completed, total) {
            var item = document.getElementById("uninstallPreviewWait" + nodeId);
            if (!item) return;
            var status = item.querySelector(".uninstall-preview-wait-status");
            status.className = "uninstall-preview-wait-status badge text-bg-" + (node.eligible ? "success" : "warning");
            status.textContent = node.eligible ? "删除路径已获取" : (node.gate_message || "未通过探测");
            document.getElementById("uninstallPreviewWaitCount").textContent = completed + " / " + total + " 台完成";
            var bar = document.getElementById("uninstallPreviewWaitBar");
            var percent = Math.round(completed * 100 / total);
            bar.style.width = percent + "%";
            bar.parentElement.setAttribute("aria-valuenow", String(completed));
        }

        function preview() {
            var ids = Object.keys(selected).map(Number);
            var requestId = ++previewRequestId;
            document.getElementById("uninstallPreviewSummary").textContent = "正在获取删除路径";
            document.getElementById("uninstallToStep3").disabled = true;
            acknowledgedForeignPaths = Object.create(null);
            renderPreviewLoading(ids);
            setStep(2);
            var results = Object.create(null);
            var completed = 0;
            var nextIndex = 0;
            var workerCount = Math.min(ids.length, Math.max(1, maxCount));

            function previewNext() {
                if (requestId !== previewRequestId || nextIndex >= ids.length) {
                    return Promise.resolve();
                }
                var nodeId = ids[nextIndex++];
                return requestJson(config.dataset.previewUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                    body: JSON.stringify({ node_ids: [nodeId] })
                }).then(function (data) {
                    var node = (data.nodes || []).find(function (candidate) { return candidate.id === nodeId; });
                    if (!node) throw new Error("预览响应缺少节点结果");
                    return node;
                }).catch(function (error) {
                    var selectedNode = selected[String(nodeId)] || {};
                    return {
                        id: nodeId,
                        hostname: selectedNode.name || "节点 " + nodeId,
                        ip: selectedNode.ip || "",
                        eligible: false,
                        gate_message: "预览失败：" + error.message,
                        paths: [],
                        install_origin: "unknown",
                        manage_mode: "unknown",
                        foreign_prefix_paths: []
                    };
                }).then(function (node) {
                    results[nodeId] = node;
                    if (requestId === previewRequestId) {
                        completed += 1;
                        updatePreviewLoading(nodeId, node, completed, ids.length);
                    }
                    return previewNext();
                });
            }

            var workers = [];
            for (var worker = 0; worker < workerCount; worker += 1) {
                workers.push(previewNext());
            }
            Promise.all(workers).then(function () {
                if (requestId !== previewRequestId) return;
                previewNodes = ids.map(function (nodeId) { return results[nodeId]; });
                renderPreview();
            });
        }

        function renderPreview() {
            var eligible = previewNodes.filter(function (node) { return node.eligible; });
            document.getElementById("uninstallPreviewSummary").textContent = eligible.length + " 台可执行，" + (previewNodes.length - eligible.length) + " 台未通过门禁";
            var root = document.getElementById("uninstallPreviewNodes");
            root.innerHTML = "";
            previewNodes.forEach(function (node) {
                var isSystemd = node.manage_mode === "systemctl";
                var mode = isSystemd ? "systemctl 托管" : (node.manage_mode === "binary" ? "二进制启动" : "托管方式未知");
                var installLabel = node.install_origin === "package"
                    ? node.package_manager + ":" + node.package_name
                    : (node.prefix || "prefix 未解析");
                var summary = node.eligible ? (node.running ? '<span class="badge text-bg-warning">运行中</span>' : '<span class="badge text-bg-success">已停止</span>') : '<span class="badge text-bg-danger">' + escapeHtml(node.gate_message || "不可执行") + "</span>";
                var heading = '<h3 class="uninstall-preview-heading"><span class="uninstall-preview-host">' + escapeHtml(node.hostname) + ' (' + escapeHtml(node.ip) + ')</span><span class="text-muted">-</span>';
                if (!isSystemd) heading += '<code class="uninstall-preview-install">' + escapeHtml(installLabel) + '</code>';
                heading += '<span class="uninstall-preview-mode">' + escapeHtml(mode) + '</span>' + summary + '</h3>';
                var pathHtml = "";
                (node.paths || []).forEach(function (path, index) {
                    var checked = path.checked ? " checked" : "";
                    var disabled = !canExecute || path.required || path.package_owned || path.key === "prefix" ? " disabled" : "";
                    var input = path.key === "prefix"
                        ? '<input class="form-control form-control-sm uninstall-prefix-input" value="' + escapeHtml(path.path) + '" data-node="' + node.id + '" aria-label="' + escapeHtml(node.hostname) + ' prefix" readonly>'
                        : '<input class="form-check-input uninstall-path-check" type="checkbox" data-node="' + node.id + '" data-path-index="' + index + '" data-key="' + escapeHtml(path.key) + '" data-path="' + escapeHtml(path.path) + '" data-kind="' + escapeHtml(path.kind) + '"' + checked + disabled + ' aria-label="选择删除路径 ' + escapeHtml(path.path) + '">';
                    var badge = path.package_owned ? '<span class="badge text-bg-info">包管理器处理</span>' : (path.required ? '<span class="badge text-bg-danger">必选</span>' : "");
                    pathHtml += '<div class="uninstall-path-row" data-node="' + node.id + '" data-path="' + escapeHtml(path.path) + '" data-kind="' + escapeHtml(path.kind) + '" data-required="' + (path.required ? "true" : "false") + '" data-package-owned="' + (path.package_owned ? "true" : "false") + '"><div class="form-check uninstall-path-toggle">' + (path.key === "prefix" ? '<span class="uninstall-path-required"><i class="bi bi-lock-fill" aria-hidden="true"></i></span>' : input) + '<span class="uninstall-path-label">' + escapeHtml(path.label) + "</span>" + badge + '</div><code class="uninstall-path-code">' + (path.key === "prefix" ? input : escapeHtml(path.path)) + '</code></div>';
                });
                var settings = node.install_origin === "package" ? '<p class="small text-muted mt-2 mb-2">软件包文件由包管理器管理，路径项不会通过 rm 删除。</p>' : "";
                var sourceBoundary = "";
                if (node.install_origin === "source" && node.prefix) {
                    var prefixParent = node.prefix.replace(/\/+$/, "").replace(/\/[^/]*$/, "") || "/";
                    sourceBoundary = '<div class="alert alert-info py-2 small mt-2 mb-2"><strong>源码删除边界：</strong>仅删除 prefix 目录 <code>' + escapeHtml(node.prefix) + '</code>，保留上级目录 <code>' + escapeHtml(prefixParent) + '</code>；prefix 内其他内容也会一并删除。</div>';
                }
                var foreignPaths = node.foreign_prefix_paths || [];
                var shallow = foreignPaths.length ? '<div class="alert alert-warning py-2 small mt-2 mb-2">' + foreignPathsDetails(foreignPaths, "prefix 中有 " + foreignPaths.length + " 条无法确认归属的路径；确认范围时将再次提示") + "</div>" : "";
                var warning = node.running_error ? '<div class="small text-warning mt-1">' + escapeHtml(node.running_error) + "</div>" : "";
                root.insertAdjacentHTML("beforeend", '<article class="uninstall-preview-node" data-node="' + node.id + '"><header class="uninstall-preview-header"><div class="uninstall-preview-header-content">' + heading + warning + '</div><button type="button" class="btn btn-sm btn-outline-secondary flex-shrink-0" data-bs-toggle="collapse" data-bs-target="#uninstallPaths' + node.id + '" aria-expanded="true" aria-label="折叠路径"><i class="bi bi-chevron-up" aria-hidden="true"></i></button></header>' + sourceBoundary + shallow + settings + '<div class="collapse show" id="uninstallPaths' + node.id + '"><div class="uninstall-path-list">' + pathHtml + "</div></div></article>");
            });
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
                    checkbox.disabled = !canExecute || row.dataset.required === "true" || row.dataset.packageOwned === "true" || parent;
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
                var removeBackup = false;
                var removeWorkdir = false;
                var removeModules = false;
                document.querySelectorAll('.uninstall-path-check[data-node="' + node.id + '"]:checked').forEach(function (checkbox) {
                    if (checkbox.disabled) return;
                    if (checkbox.dataset.key === "release_backup") removeBackup = true;
                    else if (checkbox.dataset.key === "work_dir") removeWorkdir = true;
                    else if (checkbox.dataset.key === "nginx_modules") removeModules = true;
                    else if (node.install_origin === "source" && checkbox.dataset.key.indexOf("--") === 0) {
                        extra.push({ key: checkbox.dataset.key, path: checkbox.dataset.path });
                    }
                });
                return {
                    id: node.id,
                    install_origin: node.install_origin,
                    package_manager: node.package_manager,
                    package_name: node.package_name,
                    prefix: prefixInput ? prefixInput.value.trim() : node.prefix,
                    remove_backup: removeBackup,
                    remove_workdir: removeWorkdir,
                    remove_modules: removeModules,
                    stop_if_running: true,
                    extra_paths: extra,
                    acknowledged_foreign_prefix_paths: acknowledgedForeignPaths[String(node.id)] || []
                };
            });
        }

        function continueToConfirmation() {
            var riskyNodes = previewNodes.filter(function (node) {
                return node.eligible && (node.foreign_prefix_paths || []).length > 0;
            });
            function showConfirmationStep() {
                riskyNodes.forEach(function (node) {
                    acknowledgedForeignPaths[String(node.id)] = node.foreign_prefix_paths.slice();
                });
                renderConfirmation();
                setStep(3);
            }
            if (!riskyNodes.length) {
                showConfirmationStep();
                return;
            }
            var details = riskyNodes.map(function (node) {
                var paths = foreignPathsDetails(node.foreign_prefix_paths, "展开查看 " + node.foreign_prefix_paths.length + " 条路径");
                return "<li><strong>" + escapeHtml(node.hostname) + " (" + escapeHtml(node.ip) + ")</strong><div class='small text-muted'>prefix " + escapeHtml(node.prefix) + "</div>" + paths + "</li>";
            }).join("");
            window.showConfirm(
                "确认删除非 Nginx 内容",
                "<p>以下目录项无法从 Nginx 编译参数或常见目录确认归属。继续后会随 prefix 一并删除：</p><ul class='mb-0'>" + details + "</ul>",
                showConfirmationStep,
                true,
                "lg",
                "确认范围并继续"
            );
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
                    return '<article class="uninstall-progress-item"><div class="uninstall-progress-item-header"><div class="min-w-0"><strong>' + escapeHtml(task.hostname) + '</strong><small class="d-block text-muted">' + escapeHtml(task.ip) + '</small></div><span class="badge text-bg-' + statusClass + '">' + escapeHtml(statusLabels[task.status] || task.status) + '</span></div><div class="small text-muted text-break mb-2">' + escapeHtml(task.detail || "-") + '</div><div class="d-flex align-items-center gap-2"><div class="progress flex-grow-1" style="height:5px" role="progressbar" aria-label="' + escapeHtml(task.hostname) + ' 卸载进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + task.progress + '"><div class="progress-bar' + (task.finished ? " bg-" + statusClass : " progress-bar-striped progress-bar-animated") + '" style="width:' + task.progress + '%"></div></div><small>' + task.progress + '%</small><a class="btn btn-outline-primary btn-sm" href="/nginx/uninstall/task/' + task.task_id + '/log/" aria-label="查看完整日志" title="查看完整日志"><i class="bi bi-file-text" aria-hidden="true"></i></a></div></article>';
                }).join("");
                document.getElementById("uninstallProgressRows").innerHTML = rows;
                document.getElementById("uninstallProgressBar").style.width = data.progress + "%";
                document.getElementById("uninstallProgressBar").parentElement.setAttribute("aria-valuenow", String(data.progress));
                var progressBadge = document.getElementById("uninstallProgressPercent");
                progressBadge.classList.remove("d-none", "text-bg-secondary", "text-bg-success", "text-bg-warning");
                progressBadge.textContent = data.progress + "%";
                progressBadge.classList.add(data.finished ? (data.fail_count ? "text-bg-warning" : "text-bg-success") : "text-bg-secondary");
                document.getElementById("uninstallProgressCounts").textContent = "完成 " + data.tasks.filter(function (task) { return task.finished; }).length + " / " + data.total + " · 成功 " + data.success_count + " · 失败 " + data.fail_count;
                document.getElementById("uninstallCancel").classList.toggle("d-none", data.finished);
                if (!data.finished) pollTimer = window.setTimeout(pollBatch, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                else {
                    var progressBar = document.getElementById("uninstallProgressBar");
                    progressBar.classList.remove("progress-bar-striped", "progress-bar-animated");
                    progressBar.classList.add(data.fail_count ? "bg-warning" : "bg-success");
                    window.showToast("卸载批次已完成", data.fail_count ? "warning" : "success");
                }
            }).catch(function (error) {
                document.getElementById("uninstallProgressCounts").textContent = error.message;
                pollTimer = window.setTimeout(pollBatch, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            });
        }

        bindNodePicker();
        document.getElementById("uninstallToStep2").addEventListener("click", preview);
        document.getElementById("uninstallBackToStep1").addEventListener("click", function () {
            previewRequestId += 1;
            setStep(1);
        });
        document.getElementById("uninstallBackToStep2").addEventListener("click", function () { setStep(2); });
        document.getElementById("uninstallToStep3").addEventListener("click", function () {
            var eligible = previewNodes.some(function (node) { return node.eligible; });
            var badPrefix = selectedPayload().some(function (item) { return item.install_origin === "source" && !item.prefix; });
            if (!eligible || badPrefix) { window.showAlert("路径无效", "请检查 prefix 和节点预览结果。"); return; }
            continueToConfirmation();
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
    }

    function initTaskDetail(config) {
        var taskId = config.dataset.taskId;
        var cursor = Number(config.dataset.logCursor || 0);
        var canCancel = config.dataset.canCancel === "true";
        var logs = document.getElementById("uninstallTaskLogs");
        var logFilter = document.getElementById("uninstallLogNodeFilter");
        var statusLabels = { pending: "等待执行", running: "执行中", success: "卸载成功", failed: "卸载失败", cancelled: "已取消" };

        function applyLogFilter() {
            if (!logFilter) return;
            logs.querySelectorAll(".upgrade-log-line").forEach(function (line) {
                line.hidden = !!logFilter.value && line.dataset.taskId !== logFilter.value;
            });
        }

        function appendLog(entry) {
            var empty = logs.querySelector("[data-empty-log]");
            if (empty) empty.remove();
            var line = document.createElement("div");
            line.className = "upgrade-log-line log-" + (entry.level || "info");
            line.dataset.taskId = entry.task_id == null ? "" : String(entry.task_id);
            var time = document.createElement("time");
            time.textContent = entry.created_at ? window.formatUtc8DateTime(entry.created_at) : "";
            var message = document.createElement("span");
            message.className = "upgrade-log-message";
            message.textContent = entry.message || "";
            line.append(time, message);
            logs.appendChild(line);
        }

        if (logFilter) logFilter.addEventListener("change", applyLogFilter);
        applyLogFilter();

        function poll() {
            requestJson(config.dataset.taskUrlPrefix + taskId + "?after_log_id=" + cursor).then(function (data) {
                document.getElementById("uninstallTaskStatus").textContent = statusLabels[data.status] || data.status;
                document.getElementById("uninstallTaskProgress").textContent = data.progress + "%";
                document.getElementById("uninstallTaskDetail").textContent = data.detail;
                config.dataset.batchActive = data.batch_active ? "true" : "false";
                data.logs.forEach(function (entry) {
                    appendLog(entry);
                    cursor = Math.max(cursor, entry.id);
                });
                applyLogFilter();
                var stillRunning = data.status === "pending" || data.status === "running" || data.batch_active;
                document.getElementById("uninstallLogState").textContent = data.has_more_logs ? "正在载入剩余日志" : (stillRunning ? "实时更新中" : "执行完成");
                logs.scrollTop = logs.scrollHeight;
                if (data.result_tree) document.getElementById("uninstallTaskResult").textContent = JSON.stringify(data.result_tree, null, 2);
                if (stillRunning || data.has_more_logs) window.setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                var cancel = document.getElementById("uninstallTaskCancel");
                if (cancel && data.status !== "pending" && data.status !== "running") cancel.remove();
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
        if (config.dataset.batchActive === "true" || document.getElementById("uninstallTaskStatus").textContent === "等待执行" || document.getElementById("uninstallTaskStatus").textContent === "执行中") poll();
    }
})();
