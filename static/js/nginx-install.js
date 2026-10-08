(function () {
    "use strict";

    var phaseLabels = {
        pending: "等待执行",
        checking_tools: "检查编译工具",
        uploading_package: "上传源码包",
        extracting_package: "解压源码包",
        preparing_modules: "准备第三方模块",
        configuring: "执行 configure",
        compiling: "执行 make",
        installing: "执行 make install",
        verifying: "验证安装",
        starting: "启动 Nginx",
        syncing_config: "自动同步配置",
        success: "安装完成",
        failed: "安装失败",
        cancelled: "已取消"
    };
    var statusLabels = {
        pending: "等待执行",
        running: "执行中",
        success: "安装成功",
        failed: "安装失败",
        cancelled: "已取消"
    };

    function readJson(id) {
        var node = document.getElementById(id);
        if (!node) return [];
        try {
            return JSON.parse(node.textContent || "[]");
        } catch (error) {
            return [];
        }
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : "";
    }

    function requestJson(url, options) {
        var requestOptions = Object.assign({ credentials: "same-origin" }, options || {});
        requestOptions.headers = Object.assign({}, requestOptions.headers || {});
        if (requestOptions.method && requestOptions.method.toUpperCase() !== "GET") {
            requestOptions.headers["X-CSRFToken"] = csrfToken();
        }
        return fetch(url, requestOptions).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (payload) {
                if (!response.ok) {
                    throw new Error(payload.message || "请求失败");
                }
                return payload;
            });
        });
    }

    function notify(message, type) {
        if (window.showToast) {
            window.showToast(message, type || "info");
        }
    }

    function buildNodePicker() {
        var table = document.getElementById("installNodeTable");
        if (!table) return;
        var maxNodes = Number(document.getElementById("installStepper").dataset.batchMax) || 3;
        var selected = {};
        var nodeRows = Array.prototype.slice.call(table.querySelectorAll("[data-node-row]"));
        var packageSelect = document.getElementById("sourcePackage");
        var canExecute =
            document.getElementById("installStepper").dataset.canExecute === "true";
        var modulePackages = readJson("installModulePackageData");
        var builtinModules = readJson("installBuiltinModuleData");
        var defaultModules = readJson("installDefaultModuleData");
        var chosenModules = defaultModules.filter(function (item) {
            return builtinModules.indexOf(item) >= 0;
        });
        var batchNumber = "";
        var pollTimer = null;
        var riskConfirmed = false;

        table.addEventListener("change", function (event) {
            var input = event.target.closest("[data-node-id]");
            if (!input) return;
            if (input.checked && Object.keys(selected).length >= maxNodes) {
                input.checked = false;
                notify("单次最多选择 " + maxNodes + " 台节点", "warning");
                return;
            }
            if (input.checked) {
                selected[input.dataset.nodeId] = true;
            } else {
                delete selected[input.dataset.nodeId];
            }
            riskConfirmed = false;
            updateNodeCount();
        });

        var nodeSearch = document.getElementById("nodeSearch");
        nodeSearch.addEventListener("input", function () {
            var term = nodeSearch.value.trim().toLowerCase();
            nodeRows.forEach(function (row) {
                row.hidden = term && row.dataset.nodeSearch.indexOf(term) < 0;
            });
        });

        function updateNodeCount() {
            document.getElementById("selectedNodeCount").textContent = String(Object.keys(selected).length);
            var next = document.getElementById("step1Next");
            next.disabled = !Object.keys(selected).length || !packageSelect.value;
        }

        packageSelect.addEventListener("change", updateNodeCount);
        document.getElementById("listenPort").addEventListener("input", function () {
            riskConfirmed = false;
        });
        document.getElementById("step1Next").addEventListener("click", function () {
            if (!Object.keys(selected).length || !packageSelect.value) {
                notify("请选择目标节点和 Nginx 源码包", "warning");
                return;
            }
            setStep(2);
        });

        document.querySelectorAll("[data-step-back]").forEach(function (button) {
            button.addEventListener("click", function () {
                setStep(Number(button.dataset.stepBack));
            });
        });

        buildModulePicker();
        buildThirdPartyEditor();
        document.getElementById("step2Next").addEventListener("click", goToConfirmation);
        document.getElementById("confirmInstall").addEventListener("change", function () {
            document.getElementById("startInstall").disabled =
                !canExecute || !this.checked || !!batchNumber;
        });
        document.getElementById("startInstall").addEventListener("click", confirmStart);

        function buildModulePicker() {
            var picker = document.getElementById("builtinModuleList");
            builtinModules.forEach(function (moduleName) {
                var label = document.createElement("label");
                label.className = "install-module-option";
                label.dataset.moduleSearch = moduleName.toLowerCase();
                var input = document.createElement("input");
                input.type = "checkbox";
                input.value = moduleName;
                input.checked = chosenModules.indexOf(moduleName) >= 0;
                input.className = "form-check-input";
                input.addEventListener("change", renderChosenModuleList);
                var code = document.createElement("code");
                code.textContent = moduleName;
                label.appendChild(input);
                label.appendChild(code);
                picker.appendChild(label);
            });
            document.getElementById("moduleSearch").addEventListener("input", function () {
                var term = this.value.trim().toLowerCase();
                picker.querySelectorAll(".install-module-option").forEach(function (row) {
                    row.hidden = term && row.dataset.moduleSearch.indexOf(term) < 0;
                });
            });
            document.getElementById("saveModules").addEventListener("click", function () {
                chosenModules = Array.prototype.slice.call(
                    picker.querySelectorAll("input:checked")
                ).map(function (input) { return input.value; });
                renderChosenModuleList();
                updateModuleSummary();
            });
            document.getElementById("moduleAdjustModal").addEventListener("show.bs.modal", function () {
                picker.querySelectorAll("input[type=checkbox]").forEach(function (input) {
                    input.checked = chosenModules.indexOf(input.value) >= 0;
                });
                renderChosenModuleList();
            });
            renderChosenModuleList();
            updateModuleSummary();
        }

        function renderChosenModuleList() {
            var selectedList = document.getElementById("selectedModuleList");
            selectedList.replaceChildren();
            var values = Array.prototype.slice.call(
                document.querySelectorAll("#builtinModuleList input:checked")
            ).map(function (input) { return input.value; });
            if (!values.length) {
                selectedList.textContent = "未选择官方模块";
                return;
            }
            values.forEach(function (value) {
                var row = document.createElement("div");
                row.className = "install-module-option";
                var icon = document.createElement("i");
                icon.className = "bi bi-check2 text-success";
                icon.setAttribute("aria-hidden", "true");
                var code = document.createElement("code");
                code.textContent = value;
                row.appendChild(icon);
                row.appendChild(code);
                selectedList.appendChild(row);
            });
        }

        function updateModuleSummary() {
            document.getElementById("moduleSelectedCount").textContent = String(chosenModules.length);
            document.getElementById("selectedModuleSummary").textContent = chosenModules.length
                ? chosenModules.slice(0, 3).join("  ") + (chosenModules.length > 3 ? "  等 " + chosenModules.length + " 项" : "")
                : "未选择官方模块";
        }

        function buildThirdPartyEditor() {
            var container = document.getElementById("thirdPartyModules");
            document.getElementById("addThirdParty").addEventListener("click", function () {
                container.appendChild(createThirdPartyRow());
            });
        }

        function createThirdPartyRow() {
            var row = document.createElement("div");
            row.className = "install-third-party-row";
            var name = document.createElement("input");
            name.className = "form-control form-control-sm";
            name.placeholder = "模块名称";
            name.setAttribute("aria-label", "模块名称");
            var source = document.createElement("select");
            source.className = "form-select form-select-sm";
            source.setAttribute("aria-label", "模块来源");
            [["git", "在线 Git"], ["package", "离线包"]].forEach(function (item) {
                var option = document.createElement("option");
                option.value = item[0];
                option.textContent = item[1];
                source.appendChild(option);
            });
            var remove = document.createElement("button");
            remove.className = "btn btn-outline-danger btn-sm";
            remove.type = "button";
            remove.title = "移除模块";
            remove.setAttribute("aria-label", "移除模块");
            remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
            remove.addEventListener("click", function () { row.remove(); });
            var fields = document.createElement("div");
            fields.className = "install-third-party-extra";
            var url = document.createElement("input");
            url.className = "form-control form-control-sm";
            url.placeholder = "Git 仓库地址";
            url.setAttribute("aria-label", "Git 仓库地址");
            var branch = document.createElement("input");
            branch.className = "form-control form-control-sm";
            branch.placeholder = "分支，默认 master";
            branch.value = "master";
            branch.setAttribute("aria-label", "Git 分支");
            var packageSelect = document.createElement("select");
            packageSelect.className = "form-select form-select-sm d-none";
            packageSelect.setAttribute("aria-label", "离线模块包");
            var placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = "选择离线模块包";
            packageSelect.appendChild(placeholder);
            modulePackages.forEach(function (item) {
                var option = document.createElement("option");
                option.value = String(item.id);
                option.textContent = item.name + (item.version ? " · " + item.version : "");
                packageSelect.appendChild(option);
            });
            source.addEventListener("change", updateThirdPartyFields);
            fields.appendChild(url);
            fields.appendChild(branch);
            fields.appendChild(packageSelect);
            remove.addEventListener("click", function () { row.remove(); });
            row.appendChild(name);
            row.appendChild(source);
            row.appendChild(fields);
            row.appendChild(remove);
            row.installFields = { name: name, source: source, url: url, branch: branch, packageId: packageSelect };
            updateThirdPartyFields.call(source);
            return row;
        }

        function updateThirdPartyFields() {
            var source = this.value;
            var row = this.closest(".install-third-party-row");
            var fields = row.querySelector(".install-third-party-extra");
            row.installFields.url.classList.toggle("d-none", source !== "git");
            row.installFields.branch.classList.toggle("d-none", source !== "git");
            row.installFields.packageId.classList.toggle("d-none", source !== "package");
            fields.classList.toggle("d-none", false);
        }

        function getThirdPartyModules() {
            return Array.prototype.slice.call(
                document.querySelectorAll("#thirdPartyModules .install-third-party-row")
            ).map(function (row) {
                var fields = row.installFields;
                return {
                    name: fields.name.value.trim(),
                    source: fields.source.value,
                    git_url: fields.source.value === "git" ? fields.url.value.trim() : undefined,
                    branch: fields.source.value === "git" ? (fields.branch.value.trim() || "master") : undefined,
                    package_id: fields.source.value === "package" ? Number(fields.packageId.value) : undefined
                };
            }).filter(function (item) { return item.name; });
        }

        function formValues() {
            return {
                target_prefix: document.getElementById("targetPrefix").value.trim(),
                nginx_user: document.getElementById("nginxUser").value.trim(),
                nginx_group: document.getElementById("nginxGroup").value.trim(),
                listen_port: Number(document.getElementById("listenPort").value),
                remote_work_dir: document.getElementById("remoteWorkDir").value.trim(),
                make_jobs: Number(document.getElementById("makeJobs").value),
                added_modules: chosenModules,
                added_third_party: getThirdPartyModules(),
                extra_opts: document.getElementById("extraOpts").value
            };
        }

        function selectedNodeObjects() {
            var nodes = readJson("installNodeData");
            return Object.keys(selected).map(function (id) {
                return nodes.find(function (node) { return String(node.id) === id; });
            }).filter(Boolean);
        }

        function selectedPackage() {
            var option = packageSelect.options[packageSelect.selectedIndex];
            return {
                id: Number(packageSelect.value),
                version: option ? option.dataset.version : "",
                label: option ? option.textContent : ""
            };
        }

        function renderConfirmation(configure) {
            var nodes = selectedNodeObjects();
            var pkg = selectedPackage();
            var values = formValues();
            var summary = document.getElementById("installConfirmSummary");
            summary.replaceChildren();
            [
                ["源码版本", pkg.version],
                ["安装前缀", values.target_prefix],
                ["运行账户", values.nginx_user + ":" + values.nginx_group],
                ["监听端口", String(values.listen_port)],
                ["工作目录", values.remote_work_dir],
                ["编译并发", "-j" + values.make_jobs],
                ["官方模块", String(values.added_modules.length)],
                ["第三方模块", String(values.added_third_party.length)],
                ["启动管理", "优先注册并启用 nginx.service；不可管理时使用二进制启动"]
            ].forEach(function (pair) {
                var item = document.createElement("div");
                item.className = "install-summary-item";
                var label = document.createElement("small");
                label.textContent = pair[0];
                var value = document.createElement("span");
                value.textContent = pair[1] || "-";
                item.appendChild(label);
                item.appendChild(value);
                summary.appendChild(item);
            });
            document.getElementById("installConfigurePreview").textContent = "./configure \\\n" + configure;
            var nodeList = document.getElementById("installConfirmNodes");
            nodeList.replaceChildren();
            nodes.forEach(function (node) {
                var row = document.createElement("div");
                row.className = "install-confirm-item";
                var identity = document.createElement("span");
                identity.textContent = node.hostname + " (" + node.ip + ")";
                var state = document.createElement("span");
                state.className = node.nginx_available === true ? "badge text-bg-warning" : "badge text-bg-success";
                state.textContent = node.nginx_available === true ? "已检测到 Nginx" : "SSH 在线";
                row.appendChild(identity);
                row.appendChild(state);
                nodeList.appendChild(row);
            });
            renderWarnings(nodes, values);
        }

        function renderWarnings(nodes, values) {
            var target = document.getElementById("installRiskWarnings");
            target.replaceChildren();
            var installed = nodes.filter(function (node) { return node.nginx_available === true; });
            var nonRoot = nodes.filter(function (node) {
                return node.credential_username && node.credential_username !== "root";
            });
            if (installed.length) {
                target.appendChild(makeWarning(
                    "已检测到 Nginx 的节点将继续安装，目标目录或 systemd unit 可能被覆盖：" +
                    installed.map(function (node) { return node.hostname; }).join("、")
                ));
            }
            if (values.listen_port === 80 && nonRoot.length) {
                target.appendChild(makeWarning(
                    "以下节点使用非 root SSH 用户且监听端口为 80，nginx -t 可能因特权端口权限失败：" +
                    nonRoot.map(function (node) { return node.hostname + "（" + node.credential_username + "）"; }).join("、")
                ));
            }
        }

        function makeWarning(message) {
            var alert = document.createElement("div");
            alert.className = "alert alert-warning small mb-0";
            alert.textContent = message;
            return alert;
        }

        function goToConfirmation() {
            if (!selectedPackage().id || !selectedNodeObjects().length) {
                notify("请返回第一步检查节点与源码包", "warning");
                return;
            }
            var values = formValues();
            if (!values.target_prefix || !values.remote_work_dir || !values.listen_port || !values.make_jobs) {
                notify("请完整填写安装路径、工作目录、监听端口和编译并发", "warning");
                return;
            }
            var button = document.getElementById("step2Next");
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>正在生成预览';
            requestJson("/api/nginx-install/configure-preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(values)
            }).then(function (result) {
                renderConfirmation(result.target_configure_opts);
                var nodes = selectedNodeObjects();
                var riskNodes = values.listen_port === 80 ? nodes.filter(function (node) {
                    return node.credential_username && node.credential_username !== "root";
                }) : [];
                if (riskNodes.length && !riskConfirmed) {
                    var maxShow = 10;
                    var names = riskNodes.slice(0, maxShow).map(function (node) {
                        return node.hostname + " (" + node.ip + ")";
                    }).join("\n");
                    if (riskNodes.length > maxShow) names += "\n等共 " + riskNodes.length + " 台";
                    var message = "以下节点使用非 root SSH 用户且监听端口为 80，nginx -t 可能因特权端口权限失败：\n" + names + "\n\n建议改用 1024 以上端口。是否继续到确认页？";
                    window.showConfirm("监听端口风险提示", message, function () {
                        riskConfirmed = true;
                        setStep(3);
                    });
                    return;
                }
                setStep(3);
            }).catch(function (error) {
                notify(error.message, "danger");
            }).finally(function () {
                button.disabled = false;
                button.innerHTML = '下一步 <i class="bi bi-chevron-right ms-1" aria-hidden="true"></i>';
            });
        }

        function confirmStart() {
            var values = formValues();
            var nodes = selectedNodeObjects();
            var existing = nodes.filter(function (node) { return node.nginx_available === true; });
            var nonRoot = nodes.filter(function (node) {
                return node.credential_username && node.credential_username !== "root";
            });
            var message = "将为 " + nodes.length + " 台节点安装 " + selectedPackage().version +
                "。安装完成后会尝试自动同步配置。";
            message += "\n\n启动策略：优先注册并启用 nginx.service；无法管理 systemd 时改用二进制启动。";
            if (nonRoot.length) {
                message += "\n\n以下节点使用非 root SSH 用户，将按远端权限尝试 systemctl；若无 unit 写权限且无免密 sudo，将使用二进制启动：" +
                    nonRoot.map(function (node) {
                        return node.hostname + " (" + node.ip + ", " + node.credential_username + ")";
                    }).join("、");
            }
            if (existing.length) {
                message += "\n\n已检测到 Nginx 的节点可能覆盖目标路径或 nginx.service：" +
                    existing.map(function (node) { return node.hostname; }).join("、");
            }
            window.showConfirm("开始 Nginx 安装", message, function () {
                submitInstall(values);
            }, false, "md", "开始安装");
        }

        function submitInstall(values) {
            var button = document.getElementById("startInstall");
            button.disabled = true;
            var payload = Object.assign({}, values, {
                node_ids: Object.keys(selected).map(Number),
                source_package_id: selectedPackage().id
            });
            requestJson("/api/nginx-install/tasks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(function (result) {
                batchNumber = result.batch_number;
                document.getElementById("progressPlaceholder").classList.add("d-none");
                document.getElementById("installProgressLive").classList.remove("d-none");
                document.getElementById("installBatchNumber").textContent = batchNumber;
                if (result.skipped && result.skipped.length) {
                    notify(result.skipped.map(function (item) { return item.hostname + "：" + item.reason; }).join("；"), "warning", 6000);
                }
                setStep(3);
                document.getElementById("startInstall").disabled = true;
                pollBatch();
                pollTimer = window.setInterval(pollBatch, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            }).catch(function (error) {
                button.disabled =
                    !canExecute || !document.getElementById("confirmInstall").checked;
                notify(error.message, "danger");
            });
        }

        function pollBatch() {
            if (!batchNumber) return;
            requestJson("/api/nginx-install/batches/" + encodeURIComponent(batchNumber))
                .then(renderBatch)
                .catch(function (error) {
                    if (pollTimer) window.clearInterval(pollTimer);
                    pollTimer = null;
                    notify(error.message, "danger");
                });
        }

        function renderBatch(batch) {
            var list = document.getElementById("installTaskList");
            document.getElementById("batchProgressPercent").classList.remove("d-none");
            document.getElementById("batchProgressPercent").textContent = batch.progress + "%";
            document.getElementById("batchProgressBar").style.width = batch.progress + "%";
            var existingRows = {};
            Array.prototype.slice.call(list.querySelectorAll("details[data-task-id]")).forEach(function (row) {
                existingRows[row.dataset.taskId] = row;
            });
            batch.tasks.forEach(function (task) {
                var row = existingRows[String(task.task_id)];
                if (!row) {
                    row = document.createElement("details");
                    row.className = "install-task-row";
                    row.dataset.taskId = String(task.task_id);
                    row.open = Object.keys(existingRows).length === 0;
                    var summary = document.createElement("summary");
                    var identity = document.createElement("span");
                    identity.dataset.role = "identity";
                    var badge = document.createElement("span");
                    badge.className = "badge text-bg-secondary";
                    badge.dataset.role = "status";
                    summary.appendChild(identity);
                    summary.appendChild(badge);
                    var body = document.createElement("div");
                    body.className = "install-task-row-body";
                    var progress = document.createElement("div");
                    progress.className = "progress";
                    progress.style.height = "6px";
                    var bar = document.createElement("div");
                    bar.className = "progress-bar";
                    progress.appendChild(bar);
                    var phase = document.createElement("div");
                    phase.className = "small mt-2";
                    phase.dataset.role = "phase";
                    var detail = document.createElement("p");
                    detail.className = "install-task-row-detail d-none";
                    detail.dataset.role = "detail";
                    var link = document.createElement("a");
                    link.className = "btn btn-outline-secondary btn-sm mt-2";
                    link.dataset.role = "log-link";
                    link.innerHTML = '<i class="bi bi-file-text me-1" aria-hidden="true"></i>查看完整日志';
                    body.appendChild(progress);
                    body.appendChild(phase);
                    body.appendChild(detail);
                    body.appendChild(link);
                    row.appendChild(summary);
                    row.appendChild(body);
                    list.appendChild(row);
                    existingRows[String(task.task_id)] = row;
                }
                updateTaskRow(row, task);
            });
            if (batch.finished) {
                if (pollTimer) window.clearInterval(pollTimer);
                pollTimer = null;
                var notice = document.getElementById("batchCompleteNotice");
                notice.classList.remove("d-none", "alert-success", "alert-warning");
                notice.classList.add(batch.all_success ? "alert-success" : "alert-warning");
                notice.textContent = "批次完成：成功 " + batch.success_count + "，失败或取消 " + batch.fail_count + "，共 " + batch.total + " 台。";
            }
        }

        function updateTaskRow(row, task) {
            row.querySelector('[data-role="identity"]').textContent = task.hostname + " · " + task.ip;
            var badge = row.querySelector('[data-role="status"]');
            badge.textContent = statusLabels[task.status] || task.status;
            badge.className = "badge text-bg-" + (task.status === "success" ? "success" : (task.status === "failed" ? "danger" : (task.status === "running" ? "info" : "secondary")));
            row.querySelector(".progress-bar").style.width = task.progress + "%";
            row.querySelector('[data-role="phase"]').textContent = (phaseLabels[task.phase] || task.phase) + " · " + task.progress + "%";
            var detail = row.querySelector('[data-role="detail"]');
            if (task.status === "failed" || task.status === "cancelled") {
                detail.classList.remove("d-none");
                detail.textContent = task.detail;
            } else {
                detail.classList.add("d-none");
            }
            var link = row.querySelector('[data-role="log-link"]');
            link.href = task.log_url;
        }

        function setStep(step) {
            document.querySelectorAll("[data-step-panel]").forEach(function (panel) {
                panel.classList.toggle("d-none", Number(panel.dataset.stepPanel) !== step);
            });
            document.querySelectorAll("[data-step-tab]").forEach(function (tab) {
                var current = Number(tab.dataset.stepTab);
                tab.classList.toggle("active", current === step);
                tab.classList.toggle("done", current < step);
            });
            if (step === 3) document.getElementById("installProgressLive").classList.toggle("d-none", !batchNumber);
        }
    }

    function buildTaskDetailPoller() {
        var data = document.getElementById("installTaskDetailData");
        if (!data) return;
        var taskId = Number(data.dataset.taskId);
        var batch = data.dataset.batch;
        var afterLogId = Number(data.dataset.afterLogId || 0);
        var terminal = ["success", "failed", "cancelled"];
        var logOutput = document.getElementById("installLogOutput");
        var state = document.getElementById("installLogState");
        var cancel = document.getElementById("cancelInstallTask");
        var timer = null;

        function poll() {
            Promise.all([
                requestJson("/api/tasks/" + taskId + "?after_log_id=" + afterLogId),
                requestJson("/api/nginx-install/batches/" + encodeURIComponent(batch))
            ]).then(function (results) {
                var task = results[0];
                var batchTask = results[1].tasks.find(function (item) { return item.task_id === taskId; });
                update(task, batchTask);
                if (terminal.indexOf(task.status) >= 0) {
                    if (timer) window.clearInterval(timer);
                    timer = null;
                    state.textContent = "任务已结束";
                }
            }).catch(function () {
                state.textContent = "暂时无法读取最新状态";
            });
        }

        function update(task, batchTask) {
            document.getElementById("installTaskStatus").textContent = statusLabels[task.status] || task.status;
            document.getElementById("installTaskStatus").className = "badge text-bg-" + (task.status === "success" ? "success" : (task.status === "failed" ? "danger" : (task.status === "running" ? "info" : "secondary")));
            document.getElementById("installTaskPercent").textContent = task.progress + "%";
            document.getElementById("installTaskPercent").classList.toggle("d-none", terminal.indexOf(task.status) >= 0);
            if (batchTask) {
                document.getElementById("installTaskPhase").textContent = phaseLabels[batchTask.phase] || batchTask.phase;
                if (batchTask.sync_ok !== null) {
                    document.getElementById("installSyncResult").textContent = (batchTask.sync_ok ? "成功 · " : "失败 · ") + batchTask.sync_detail;
                }
                if (cancel) cancel.classList.toggle("d-none", !["pending", "checking_tools", "uploading_package", "extracting_package", "preparing_modules"].includes(batchTask.phase) || terminal.indexOf(task.status) >= 0);
            }
            task.logs.forEach(function (item) {
                var line = document.createElement("div");
                line.className = "install-log-line log-" + item.level;
                var time = document.createElement("time");
                time.textContent = new Date(item.created_at).toLocaleString();
                var message = document.createElement("span");
                message.textContent = item.message;
                line.appendChild(time);
                line.appendChild(message);
                var atBottom = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight < 60;
                logOutput.appendChild(line);
                if (atBottom) logOutput.scrollTop = logOutput.scrollHeight;
            });
            afterLogId = task.next_log_id;
        }

        if (cancel) {
            cancel.addEventListener("click", function () {
                window.showConfirm("取消安装任务", "仅能在编译开始前取消。已发出的远端命令可能会运行到当前检查点。", function () {
                    requestJson("/api/tasks/" + taskId + "/cancel", { method: "POST" })
                        .then(function () { notify("安装任务已取消", "success"); poll(); })
                        .catch(function (error) { notify(error.message, "danger"); });
                });
            });
        }
        poll();
        timer = window.setInterval(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
    }

    function startInstallPage() {
        if (document.getElementById("installNodeTable")) buildNodePicker();
        buildTaskDetailPoller();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startInstallPage);
    } else {
        startInstallPage();
    }
})();
