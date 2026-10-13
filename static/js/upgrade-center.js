(function ($, window, document) {
    "use strict";

    var center = document.getElementById("upgradeStepper");
    var detail = document.getElementById("upgradeTaskDetail");
    var batchMax = center ? Number(center.dataset.batchMax) || 3 : 3;
    var token = document.querySelector("meta[name='csrf-token']");
    var csrfToken = token ? token.content : "";
    var currentStep = 1;
    var environmentComplete = false;
    var previewReady = false;
    var selectedTargetSignature = "";
    var referenceNodeId = null;
    var baselineRequestVersion = 0;
    var previewRequestVersion = 0;
    var currentMismatchSignature = "";
    var acknowledgedMismatchSignature = "";
    var targetConfigPreviews = {};
    var moduleChanges = { added: [], removed: [] };
    var moduleAdjustModal = null;
    var mismatchModal = null;

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
        var response = error && error.responseJSON;
        if (response) {
            if (typeof response.message === "string" && response.message.trim()) {
                return response.message;
            }
            if (typeof response.detail === "string" && response.detail.trim()) {
                return response.detail;
            }
            if (Array.isArray(response.errors) && response.errors.length) {
                var firstError = response.errors[0];
                if (firstError && typeof firstError.message === "string") {
                    return firstError.message;
                }
            }
        }
        return error && error.status
            ? "请求失败（HTTP " + error.status + "），请检查输入后重试"
            : "请求失败，请检查输入后重试";
    }

    function notify(message, type, action) {
        if (window.showToast) window.showToast(message, type || "info", 3000, action);
    }

    function stepOneComplete() {
        return selectedNodeIds().length > 0 && !!selectedPackage();
    }

    function normalizeAbsolutePath(value) {
        var path = (value || "").trim();
        if (!path.startsWith("/") || /[\x00\r\n]/.test(path)) return "";
        var parts = [];
        path.split("/").forEach(function (part) {
            if (!part || part === ".") return;
            if (part === "..") parts.pop();
            else parts.push(part);
        });
        return "/" + parts.join("/");
    }

    function environmentError() {
        var jobs = Number(document.getElementById("makeJobs").value);
        if (!Number.isInteger(jobs) || jobs < 1 || jobs > 32) {
            return "并行编译数必须在 1 到 32 之间";
        }
        if (!normalizeAbsolutePath(currentWorkDir()) || normalizeAbsolutePath(currentWorkDir()) === "/") {
            return "远程工作目录必须是有效的非根绝对路径";
        }
        if (currentMode() === "switch_path") {
            var prefix = normalizeAbsolutePath(document.getElementById("targetPrefix").value);
            if (!prefix || prefix === "/") return "切换路径模式请填写有效的目标安装目录";
        }
        return "";
    }

    function refreshStepperAccess() {
        var unlocked = 1;
        if (stepOneComplete()) unlocked = 2;
        if (stepOneComplete() && environmentComplete && !environmentError()) unlocked = 3;
        if (stepOneComplete() && environmentComplete && previewReady) unlocked = 4;
        document.querySelectorAll(".upgrade-step").forEach(function (item) {
            var locked = Number(item.dataset.stepTarget) > unlocked;
            item.disabled = locked;
            item.setAttribute("aria-disabled", locked ? "true" : "false");
        });
    }

    function activateStep(step) {
        var unlocked = 1;
        if (stepOneComplete()) unlocked = 2;
        if (stepOneComplete() && environmentComplete && !environmentError()) unlocked = 3;
        if (stepOneComplete() && environmentComplete && previewReady) unlocked = 4;
        if (step > unlocked) return false;
        currentStep = step;
        document.querySelectorAll(".upgrade-panel").forEach(function (panel) {
            panel.classList.toggle("d-none", Number(panel.dataset.step) !== step);
        });
        document.querySelectorAll(".upgrade-step").forEach(function (item) {
            var itemStep = Number(item.dataset.stepTarget);
            item.classList.toggle("active", itemStep === step);
            item.classList.toggle("done", itemStep < step);
        });
        window.scrollTo({ top: 0, behavior: "smooth" });
        return true;
    }

    function goToStep(step) {
        if (step === 1) {
            activateStep(1);
            return;
        }
        if (!stepOneComplete()) {
            notify("请先选择至少一个目标节点和源码包", "warning");
            activateStep(1);
            return;
        }
        if (step === 2) {
            activateStep(2);
            return;
        }
        if (!environmentComplete || environmentError()) {
            notify("请先完成并确认编译环境", "warning");
            activateStep(2);
            return;
        }
        if (step === 3) {
            if (activateStep(3)) fetchConfigureBaselines(false);
            return;
        }
        if (step === 4 && previewReady) activateStep(4);
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
            return {
                id: Number(checkbox.value),
                name: row.querySelector(".upgrade-node-hostname").textContent,
                ip: row.dataset.ip,
                nginx_path: row.dataset.nginxPath || ""
            };
        });
    }

    function selectedPackage() {
        var input = document.querySelector(".upgrade-package-check:checked");
        if (!input) return null;
        var row = input.closest(".upgrade-package-row");
        if (!row) return null;
        return { id: Number(input.value), version: input.dataset.version, name: input.dataset.name };
    }

    /** 将已选节点显示为可移除的摘要标签。 */
    function renderSelectedNodeSummary() {
        var summary = document.getElementById("selectedNodeSummary");
        var nodes = selectedNodes();
        summary.replaceChildren();
        if (!nodes.length) {
            summary.textContent = "尚未选择节点";
            return;
        }
        nodes.forEach(function (node) {
            var chip = document.createElement("span");
            chip.className = "upgrade-selected-chip";
            var label = document.createElement("span");
            label.textContent = node.name + " (" + node.ip + ")";
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "upgrade-chip-remove";
            remove.title = "移除节点";
            remove.setAttribute("aria-label", "移除节点 " + node.name);
            remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
            remove.addEventListener("click", function () {
                var checkbox = document.querySelector(
                    ".upgrade-node-check[value='" + node.id + "']"
                );
                if (checkbox) {
                    checkbox.checked = false;
                    updateSelection();
                }
            });
            chip.append(label, remove);
            summary.appendChild(chip);
        });
    }

    function updateSelection() {
        var checks = Array.from(document.querySelectorAll(".upgrade-node-check"));
        var selected = selectedNodeIds();
        checks.forEach(function (input) {
            input.disabled = !input.checked && selected.length >= batchMax;
        });
        document.getElementById("stepOneCount").textContent = "已选 " + selected.length + " 个节点";
        renderSelectedNodeSummary();
        var selectedPackageValue = selectedPackage();
        var signature = selected.slice().sort(function (a, b) { return a - b; }).join(",") + ":" +
            (selectedPackageValue ? selectedPackageValue.id : "");
        if (signature !== selectedTargetSignature) {
            selectedTargetSignature = signature;
            environmentComplete = false;
            previewReady = false;
            targetConfigPreviews = {};
            referenceNodeId = null;
            moduleChanges = { added: [], removed: [] };
            window.upgradeBaselines = {};
            currentMismatchSignature = "";
            acknowledgedMismatchSignature = "";
            baselineRequestVersion += 1;
            previewRequestVersion += 1;
            document.getElementById("confirmUpgrade").checked = false;
            var startButton = document.getElementById("startUpgrade");
            if (startButton) startButton.disabled = true;
            invalidateStepThreePreview();
        }
        document.getElementById("selectedPackageSummary").textContent = selectedPackageValue
            ? "已选源码包：" + selectedPackageValue.name + " · " + selectedPackageValue.version
            : "尚未选择源码包";
        document.getElementById("stepOneNext").disabled = selected.length === 0 || !selectedPackageValue;
        refreshStepperAccess();
    }

    function bindSelectorTable(inputId, rowSelector, key) {
        var input = document.getElementById(inputId);
        var rows = Array.from(document.querySelectorAll(rowSelector));
        var pageSize = document.getElementById(key + "PageSize");
        var first = document.getElementById(key + "PageFirst");
        var previous = document.getElementById(key + "PagePrevious");
        var next = document.getElementById(key + "PageNext");
        var last = document.getElementById(key + "PageLast");
        var count = document.getElementById(key + "ResultCount");
        var pageLabel = document.getElementById(key + "PageLabel");
        var page = 1;

        function matchingRows() {
            var terms = window.getQueryTagValue("#" + inputId)
                .split(/[,，]/).map(function (term) { return term.trim().toLocaleLowerCase(); })
                .filter(Boolean);
            return rows.filter(function (row) {
                var text = (row.dataset.search || "").toLocaleLowerCase();
                return terms.every(function (term) { return text.includes(term); });
            });
        }

        function render() {
            var matching = matchingRows();
            var size = Number(pageSize.value) || 10;
            var pages = Math.max(1, Math.ceil(matching.length / size));
            page = Math.min(page, pages);
            var start = (page - 1) * size;
            rows.forEach(function (row) { row.classList.add("d-none"); });
            matching.slice(start, start + size).forEach(function (row) { row.classList.remove("d-none"); });
            count.textContent = "共 " + matching.length + " 条";
            pageLabel.textContent = page + " / " + pages;
            first.disabled = page <= 1;
            previous.disabled = page <= 1;
            next.disabled = page >= pages;
            last.disabled = page >= pages;
            updateSelection();
        }

        input.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") return;
            event.preventDefault();
            window.commitQueryTagValue("#" + inputId);
            page = 1;
            render();
        });
        $(input).on("querytags:change", function () { page = 1; render(); });
        pageSize.addEventListener("change", function () { page = 1; render(); });
        first.addEventListener("click", function () { page = 1; render(); });
        previous.addEventListener("click", function () { page = Math.max(1, page - 1); render(); });
        next.addEventListener("click", function () { page += 1; render(); });
        last.addEventListener("click", function () {
            page = Math.max(1, Math.ceil(matchingRows().length / (Number(pageSize.value) || 10)));
            render();
        });
        if (key === "node" || key === "package") {
            rows.forEach(function (row) {
                row.addEventListener("click", function (event) {
                    if (event.target.closest("input, button, a, label, select")) return;
                    var checkbox = row.querySelector(key === "node" ? ".upgrade-node-check" : ".upgrade-package-check");
                    if (!checkbox || (checkbox.disabled && !checkbox.checked)) return;
                    if (key === "node") checkbox.checked = !checkbox.checked;
                    else checkbox.checked = true;
                    checkbox.dispatchEvent(new Event("change", { bubbles: true }));
                });
            });
        }
        render();
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
        if (force) {
            currentMismatchSignature = "";
            acknowledgedMismatchSignature = "";
            if (mismatchModal) mismatchModal.hide();
        }
        var requestVersion = ++baselineRequestVersion;
        window.upgradeBaselines = {};
        ids.forEach(function (id) {
            window.upgradeBaselines[id] = { ok: false, pending: true, message: "正在读取" };
        });
        renderFetchResults();
        document.getElementById("fetchStatus").textContent = "正在逐台读取 nginx -V，请保持页面打开…";
        document.getElementById("stepThreeNext").disabled = true;
        if (!ids.length) {
            renderFetchResults();
            return;
        }
        var requests = ids.map(function (id) {
            return Promise.resolve(requestJson("POST", "/api/upgrade/nodes/" + id + "/nginx-v", {})).then(
                function (response) { return { id: id, response: response }; },
                function (error) { return { id: id, error: error }; }
            );
        });
        Promise.all(requests).then(function (results) {
            if (requestVersion !== baselineRequestVersion) return;
            results.forEach(function (result) {
                if (result.error) {
                    window.upgradeBaselines[result.id] = { ok: false, message: errorMessage(result.error) };
                } else {
                    saveBaseline(result.id, result.response);
                }
            });
            renderFetchResults();
        });
    }

    function saveBaseline(nodeId, response) {
        window.upgradeBaselines[nodeId] = response && response.data
            ? { ok: true, data: response.data }
            : { ok: false, message: "nginx -V 未返回参数" };
    }

    function appendRecognitionDetail(details, label, value) {
        var item = document.createElement("div");
        var term = document.createElement("dt");
        term.textContent = label;
        var description = document.createElement("dd");
        var code = document.createElement("code");
        code.textContent = value || "-";
        description.appendChild(code);
        item.append(term, description);
        details.appendChild(item);
    }

    function createNodeRecognitionDetailRow(node, baseline, idPrefix) {
        var data = baseline && baseline.ok ? baseline.data : {};
        var detailRow = document.createElement("tr");
        detailRow.className = "upgrade-node-recognition-detail-row d-none";
        detailRow.id = idPrefix + node.id;
        var detailCell = document.createElement("td");
        detailCell.colSpan = 4;
        var details = document.createElement("dl");
        details.className = "upgrade-node-recognition-details mb-0";
        appendRecognitionDetail(details, "主机名 / IP", node.name + " / " + node.ip);
        appendRecognitionDetail(details, "安装目录", data.prefix);
        appendRecognitionDetail(details, "二进制路径", data.runtime_binary_path);
        appendRecognitionDetail(details, "configure 安装目标", data.binary_path);
        appendRecognitionDetail(details, "版本", data.version);
        detailCell.appendChild(details);
        if (baseline && baseline.ok) {
            var output = document.createElement("pre");
            output.className = "nginx-v-output upgrade-node-recognition-output";
            output.textContent = formatConfigurePreview(data.params || []);
            detailCell.appendChild(output);
        } else {
            var error = document.createElement("p");
            error.className = "small " + (baseline && baseline.pending ? "text-muted" : "text-danger") + " mt-2 mb-0";
            error.textContent = baseline ? baseline.message : "等待读取";
            detailCell.appendChild(error);
        }
        detailRow.appendChild(detailCell);
        return detailRow;
    }

    function renderFetchResults() {
        var selected = selectedNodes();
        var results = document.getElementById("nodeFetchResults");
        results.replaceChildren();
        if (!selected.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = document.createElement("td");
            emptyCell.colSpan = 4;
            emptyCell.className = "text-center text-muted py-3";
            emptyCell.textContent = "请选择节点后读取识别信息。";
            emptyRow.appendChild(emptyCell);
            results.appendChild(emptyRow);
        }
        selected.forEach(function (node) {
            var baseline = window.upgradeBaselines[node.id];
            var data = baseline && baseline.ok ? baseline.data : {};
            var row = document.createElement("tr");
            var hostCell = document.createElement("td");
            var host = document.createElement("div");
            host.className = "upgrade-node-recognition-host";
            var identity = document.createElement("div");
            identity.className = "upgrade-node-recognition-host-identity";
            var hostName = document.createElement("span");
            hostName.className = "upgrade-node-recognition-host-name";
            hostName.textContent = node.name;
            hostName.title = node.name;
            var hostAddress = document.createElement("small");
            hostAddress.className = "text-muted upgrade-node-recognition-host-address";
            hostAddress.textContent = node.ip;
            hostAddress.title = node.ip;
            identity.append(hostName, hostAddress);
            host.appendChild(identity);
            if (!baseline || !baseline.ok) {
                var state = document.createElement("span");
                state.className = "badge " + (baseline && baseline.pending ? "text-bg-info" : "text-bg-danger") + " ms-1";
                state.textContent = baseline && baseline.pending ? "读取中" : (baseline ? "读取失败" : "等待读取");
                hostCell.append(host, state);
            } else {
                hostCell.appendChild(host);
            }
            row.appendChild(hostCell);
            [data.prefix, data.runtime_binary_path || node.nginx_path, data.version].forEach(function (value) {
                var cell = document.createElement("td");
                var text = document.createElement("span");
                text.className = "upgrade-node-recognition-value";
                text.textContent = value || "-";
                text.title = value || "-";
                cell.appendChild(text);
                row.appendChild(cell);
            });
            results.appendChild(row);
        });
        var baselines = selected.map(function (node) { return window.upgradeBaselines[node.id]; });
        var allReady = baselines.length > 0 && baselines.every(function (item) { return item && item.ok; });
        var successCount = baselines.filter(function (item) { return item && item.ok; }).length;
        var pendingCount = baselines.filter(function (item) { return item && item.pending; }).length;
        document.getElementById("stepThreeNext").disabled = !allReady;
        var signatures = baselines.map(function (item) {
            return item && item.ok ? item.data.params.slice().sort().join("\n") : "";
        });
        var mismatch = allReady && signatures.some(function (value) { return value !== signatures[0]; });
        currentMismatchSignature = mismatch
            ? selected.map(function (node) {
                return String(node.id) + ":" + window.upgradeBaselines[node.id].data.params.slice().sort().join("\n");
            }).join("\n--node--\n")
            : "";
        document.getElementById("fetchStatus").textContent = allReady
            ? "已识别 " + selected.length + " 台节点；" + (mismatch ? "编译参数存在差异。" : "编译参数一致。")
            : pendingCount
                ? "正在读取 " + pendingCount + " 台节点的 nginx -V。"
                : successCount
                ? "已识别 " + successCount + " / " + selected.length + " 台；失败节点可重新读取或返回移除。"
                : selected.length
                    ? "节点识别失败；请检查节点连接后重新读取。"
                    : "请选择节点后读取识别信息。";
        document.getElementById("paramsMismatchNotice").classList.toggle("d-none", !mismatch);
        renderParameterMismatch(selected, mismatch);
        if (mismatch && acknowledgedMismatchSignature !== currentMismatchSignature) {
            showParameterMismatchModal(selected);
        }
        document.getElementById("stepThreeNext").disabled = !allReady ||
            (mismatch && acknowledgedMismatchSignature !== currentMismatchSignature);
        var referenceWrap = document.getElementById("referenceNodeWrap");
        referenceWrap.classList.toggle("d-none", !mismatch);
        var referenceSelect = document.getElementById("referenceNode");
        referenceSelect.replaceChildren();
        if (!allReady) {
            document.getElementById("currentConfigOutput").textContent = "读取完成后显示参考节点的 configure 参数。";
            document.getElementById("currentConfigLabel").textContent = "参考节点 configure 参数";
            document.getElementById("openModuleAdjust").disabled = true;
            return;
        }
        selected.forEach(function (node) {
            var option = document.createElement("option");
            option.value = String(node.id);
            option.textContent = node.name + " (" + node.ip + ")";
            referenceSelect.appendChild(option);
        });
        if (!selected.some(function (node) { return String(node.id) === String(referenceNodeId); })) {
            referenceNodeId = String(selected[0].id);
        }
        referenceSelect.value = referenceNodeId;
        referenceSelect.disabled = !mismatch;
        renderReferenceBaseline();
        document.getElementById("openModuleAdjust").disabled = false;
    }

    function renderParameterMismatch(nodes, mismatch) {
        var list = document.getElementById("paramsMismatchDetails");
        list.replaceChildren();
        list.classList.toggle("d-none", !mismatch);
        if (!mismatch) return;
        var params = new Set();
        nodes.forEach(function (node) {
            var baseline = window.upgradeBaselines[node.id];
            (baseline && baseline.ok ? baseline.data.params : []).forEach(function (param) {
                params.add(param);
            });
        });
        Array.from(params).sort().forEach(function (param) {
            var present = [];
            var missing = [];
            nodes.forEach(function (node) {
                var baseline = window.upgradeBaselines[node.id];
                var current = baseline && baseline.ok ? baseline.data.params : [];
                (current.indexOf(param) >= 0 ? present : missing).push(node.name);
            });
            if (!present.length || !missing.length) return;
            var item = document.createElement("div");
            item.className = "upgrade-param-diff-item";
            var value = document.createElement("code");
            value.textContent = param;
            var hosts = document.createElement("span");
            hosts.className = "text-muted";
            hosts.textContent = "已编译：" + present.join("、") + "；未编译：" + missing.join("、");
            item.append(value, hosts);
            list.appendChild(item);
        });
        if (!list.childElementCount) {
            var empty = document.createElement("span");
            empty.className = "small text-muted";
            empty.textContent = "安装路径或版本等信息不同，具体参数项一致。";
            list.appendChild(empty);
        }
    }

    function showParameterMismatchModal(nodes) {
        var body = document.getElementById("paramsMismatchNodes");
        body.replaceChildren();
        nodes.forEach(function (node) {
            var baseline = window.upgradeBaselines[node.id].data;
            var row = document.createElement("tr");
            var nodeCell = document.createElement("td");
            var expand = document.createElement("button");
            expand.type = "button";
            expand.className = "btn btn-link btn-sm p-0 text-start";
            expand.textContent = node.name;
            expand.title = node.ip;
            expand.setAttribute("aria-expanded", "false");
            var detailId = "mismatchParams-" + node.id;
            expand.setAttribute("aria-controls", detailId);
            var detailRow = createNodeRecognitionDetailRow(node, { ok: true, data: baseline }, "mismatchParams-");
            expand.addEventListener("click", function () {
                var expanded = detailRow.classList.toggle("d-none");
                expand.setAttribute("aria-expanded", expanded ? "true" : "false");
                expand.setAttribute("aria-label", (expanded ? "收起 " : "展开 ") + node.name + " 的完整识别信息");
            });
            nodeCell.appendChild(expand);
            var address = document.createElement("small");
            address.className = "d-block text-muted";
            address.textContent = node.ip;
            nodeCell.appendChild(address);
            row.appendChild(nodeCell);
            [baseline.prefix, baseline.runtime_binary_path, baseline.version].forEach(function (value) {
                var cell = document.createElement("td");
                var text = document.createElement("span");
                text.className = "upgrade-node-recognition-value";
                text.textContent = value || "-";
                text.title = value || "-";
                cell.appendChild(text);
                row.appendChild(cell);
            });
            body.append(row, detailRow);
        });

        var paramSet = new Set();
        nodes.forEach(function (node) {
            (window.upgradeBaselines[node.id].data.params || []).forEach(function (param) {
                paramSet.add(param);
            });
        });
        var differences = document.getElementById("paramsMismatchDiffs");
        differences.replaceChildren();
        Array.from(paramSet).sort().forEach(function (param) {
            var present = [];
            var missing = [];
            nodes.forEach(function (node) {
                var params = window.upgradeBaselines[node.id].data.params || [];
                (params.indexOf(param) >= 0 ? present : missing).push(node.name);
            });
            if (!present.length || !missing.length) return;
            var item = document.createElement("div");
            item.className = "upgrade-mismatch-diff-item";
            var code = document.createElement("code");
            code.textContent = param;
            var hosts = document.createElement("div");
            hosts.className = "small";
            var hasLabel = document.createElement("span");
            hasLabel.className = "text-muted me-1";
            hasLabel.textContent = "已编译：";
            hosts.appendChild(hasLabel);
            present.forEach(function (name) {
                var badge = document.createElement("span");
                badge.className = "badge text-bg-success me-1";
                badge.textContent = name;
                hosts.appendChild(badge);
            });
            var missingLabel = document.createElement("span");
            missingLabel.className = "text-muted me-1 ms-2";
            missingLabel.textContent = "未编译：";
            hosts.appendChild(missingLabel);
            missing.forEach(function (name) {
                var badge = document.createElement("span");
                badge.className = "badge text-bg-secondary me-1";
                badge.textContent = name;
                hosts.appendChild(badge);
            });
            item.append(code, hosts);
            differences.appendChild(item);
        });
        document.getElementById("paramsMismatchDiffCount").textContent =
            differences.childElementCount ? "共 " + differences.childElementCount + " 项" : "未解析到具体差异项";
        if (!mismatchModal) {
            mismatchModal = new window.bootstrap.Modal(document.getElementById("paramsMismatchModal"));
        }
        mismatchModal.show();
    }

    function referenceBaseline() {
        var value = window.upgradeBaselines[referenceNodeId];
        return value && value.ok ? value.data : null;
    }

    function renderReferenceBaseline() {
        var baseline = referenceBaseline();
        var node = selectedNodes().find(function (item) { return String(item.id) === String(referenceNodeId); });
        if (!baseline || !node) return;
        document.getElementById("currentConfigOutput").textContent =
            baseline.params && baseline.params.length
                ? formatConfigurePreview(baseline.params)
                : "当前节点未返回 configure 参数。";
        document.getElementById("currentConfigLabel").textContent =
            "当前 configure 参数（参考节点：" + node.name + "）";
        reconcileModuleChanges(baseline.params || []);
        renderModuleOptions();
        updateModuleSummary(false);
    }

    function reconcileModuleChanges(currentParams) {
        var current = new Set(currentParams);
        var builtin = new Set(window.NGXOPS_BUILTIN_MODULES || []);
        moduleChanges.added = moduleChanges.added.filter(function (item) {
            return builtin.has(item) && !current.has(item);
        });
        moduleChanges.removed = moduleChanges.removed.filter(function (item) {
            return current.has(item);
        });
    }

    function makeModuleOption(value, className, checked, disabled) {
        var label = document.createElement("label");
        label.className = "upgrade-module-option" + (disabled ? " disabled" : "");
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = className;
        checkbox.value = value;
        checkbox.checked = checked;
        checkbox.disabled = disabled;
        var code = document.createElement("code");
        code.textContent = value;
        label.append(checkbox, code);
        label.dataset.search = value.toLocaleLowerCase();
        if (disabled) {
            var badge = document.createElement("span");
            badge.className = "badge text-bg-secondary ms-auto";
            badge.textContent = "已编译";
            label.appendChild(badge);
        }
        return label;
    }

    function renderModuleOptions() {
        var baseline = referenceBaseline();
        if (!baseline) return;
        var current = new Set(baseline.params || []);
        var builtin = window.NGXOPS_BUILTIN_MODULES || [];
        var addList = document.getElementById("addModuleOptions");
        var removeList = document.getElementById("removeModuleOptions");
        addList.replaceChildren();
        removeList.replaceChildren();
        builtin.forEach(function (flag) {
            var alreadyEnabled = current.has(flag);
            addList.appendChild(makeModuleOption(
                flag, "add-module-check", !alreadyEnabled && moduleChanges.added.indexOf(flag) >= 0, alreadyEnabled
            ));
        });
        (baseline.params || []).forEach(function (param) {
            removeList.appendChild(makeModuleOption(
                param, "remove-module-check", moduleChanges.removed.indexOf(param) >= 0, false
            ));
        });
        filterModuleOptions();
    }

    function filterModuleOptions() {
        var groups = [
            {
                input: "addModuleSearch",
                selector: "#addModuleOptions .upgrade-module-option",
                hint: "addModuleSearchHint",
                opposite: "#removeModuleOptions .upgrade-module-option",
                message: "当前参考节点已编译该参数，可在右侧查看或移除。"
            },
            {
                input: "removeModuleSearch",
                selector: "#removeModuleOptions .upgrade-module-option",
                hint: "removeModuleSearchHint",
                opposite: "#addModuleOptions .upgrade-module-option",
                message: "当前参考节点未编译该参数，可在左侧查看或新增。"
            }
        ];
        groups.forEach(function (group) {
            var terms = window.getQueryTagValue("#" + group.input)
                .split(/[,，]/).map(function (term) { return term.trim().toLocaleLowerCase(); })
                .filter(Boolean);
            var rows = Array.from(document.querySelectorAll(group.selector));
            rows.forEach(function (row) {
                var text = row.dataset.search || "";
                row.classList.toggle("d-none", !terms.every(function (term) { return text.includes(term); }));
            });
            var visibleAction = rows.some(function (row) {
                var checkbox = row.querySelector("input");
                return !row.classList.contains("d-none") && !(checkbox && checkbox.disabled);
            });
            var foundOpposite = terms.length && Array.from(document.querySelectorAll(group.opposite)).some(function (row) {
                var text = row.dataset.search || "";
                return terms.every(function (term) { return text.includes(term); });
            });
            var hint = document.getElementById(group.hint);
            hint.textContent = !visibleAction && foundOpposite ? group.message : "";
            hint.classList.toggle("d-none", !hint.textContent);
        });
    }

    function collectModuleChanges() {
        return { added: moduleChanges.added.slice(), removed: moduleChanges.removed.slice() };
    }

    function updateModuleSummary(invalidatePreview) {
        document.getElementById("moduleRemovedCount").textContent = String(moduleChanges.removed.length);
        document.getElementById("moduleAddedCount").textContent = String(moduleChanges.added.length);
        document.getElementById("moduleDeltaHint").textContent =
            moduleChanges.added.length || moduleChanges.removed.length
                ? "以下变化相对参考节点；实际变化按各节点自身编译基线应用"
                : "尚未调整";
        renderModuleDiff();
        document.getElementById("stepThreeNext").title =
            "新增 " + moduleChanges.added.length + " 项，移除 " + moduleChanges.removed.length + " 项";
        if (invalidatePreview) invalidateStepThreePreview();
    }

    function appendModuleDiffGroup(container, label, values, kind, sign) {
        var group = document.createElement("section");
        group.className = "upgrade-module-diff-group is-" + kind;
        var heading = document.createElement("h4");
        heading.className = "upgrade-module-diff-title";
        heading.textContent = label;
        var list = document.createElement("ul");
        list.className = "upgrade-module-diff-list";
        if (!values.length) {
            var empty = document.createElement("li");
            empty.className = "small text-muted";
            empty.textContent = "无";
            list.appendChild(empty);
        }
        values.forEach(function (value) {
            var item = document.createElement("li");
            item.className = "upgrade-module-diff-item is-" + kind;
            var marker = document.createElement("span");
            marker.setAttribute("aria-hidden", "true");
            marker.textContent = sign;
            var code = document.createElement("code");
            code.textContent = value;
            if (kind === "removed") {
                var deleted = document.createElement("del");
                deleted.appendChild(code);
                item.append(marker, deleted);
            } else {
                item.append(marker, code);
            }
            list.appendChild(item);
        });
        group.append(heading, list);
        container.appendChild(group);
    }

    function renderModuleDiff() {
        var diff = document.getElementById("moduleDiffList");
        diff.replaceChildren();
        var hasChanges = moduleChanges.added.length || moduleChanges.removed.length;
        diff.classList.toggle("d-none", !hasChanges);
        if (!hasChanges) return;
        appendModuleDiffGroup(diff, "移除", moduleChanges.removed, "removed", "-");
        appendModuleDiffGroup(diff, "新增", moduleChanges.added, "added", "+");
    }

    function invalidateStepThreePreview() {
        previewReady = false;
        targetConfigPreviews = {};
        previewRequestVersion += 1;
        var ready = environmentComplete && !environmentError() && selectedNodeIds().length > 0 && selectedNodeIds().every(function (id) {
            return window.upgradeBaselines[id] && window.upgradeBaselines[id].ok;
        });
        var mismatchUnacknowledged = currentMismatchSignature &&
            acknowledgedMismatchSignature !== currentMismatchSignature;
        document.getElementById("stepThreeNext").disabled = !ready || !!mismatchUnacknowledged;
        refreshStepperAccess();
    }

    function openModuleAdjust() {
        if (!referenceBaseline()) {
            notify("请先读取 nginx -V 编译参数", "warning");
            return;
        }
        renderModuleOptions();
        window.clearQueryTagValue("#addModuleSearch");
        window.clearQueryTagValue("#removeModuleSearch");
        filterModuleOptions();
        if (!moduleAdjustModal) {
            moduleAdjustModal = new window.bootstrap.Modal(document.getElementById("moduleAdjustModal"));
        }
        moduleAdjustModal.show();
    }

    function confirmModuleAdjust() {
        moduleChanges.added = Array.from(document.querySelectorAll(".add-module-check:checked"))
            .map(function (input) { return input.value; });
        moduleChanges.removed = Array.from(document.querySelectorAll(".remove-module-check:checked"))
            .map(function (input) { return input.value; });
        updateModuleSummary(true);
        if (moduleAdjustModal) moduleAdjustModal.hide();
    }

    function addThirdPartyRow() {
        if (document.querySelectorAll(".upgrade-third-party-row").length >= 20) {
            notify("第三方模块最多添加 20 项", "warning");
            return;
        }
        var template = document.getElementById("thirdPartyTemplate");
        var fragment = template.content.cloneNode(true);
        var row = fragment.querySelector(".upgrade-third-party-row");
        var source = row.querySelector(".tp-source");
        var name = row.querySelector(".tp-name");
        var packageSelect = row.querySelector(".tp-package");
        var selectedPackageName = "";
        source.addEventListener("change", function () {
            row.querySelector(".tp-git-fields").classList.toggle("d-none", source.value !== "git");
            row.querySelector(".tp-package-fields").classList.toggle("d-none", source.value !== "package");
        });
        packageSelect.addEventListener("change", function () {
            var nextName = packageSelect.selectedOptions[0]
                ? packageSelect.selectedOptions[0].dataset.name || ""
                : "";
            if (!name.value || name.value === selectedPackageName) {
                name.value = nextName;
            }
            selectedPackageName = nextName;
        });
        row.querySelector(".tp-remove").addEventListener("click", function () { row.remove(); });
        document.getElementById("thirdPartyRows").appendChild(fragment);
        invalidateStepThreePreview();
    }

    function collectThirdParty() {
        var result = [];
        var errors = [];
        var names = new Set();
        document.querySelectorAll(".upgrade-third-party-row").forEach(function (row) {
            var source = row.querySelector(".tp-source").value;
            var name = row.querySelector(".tp-name").value.trim();
            var gitUrl = row.querySelector(".tp-git").value.trim();
            var packageId = row.querySelector(".tp-package").value;
            if (source === "package") {
                if (!name && !packageId) return;
                if (!name && packageId) {
                    var selectedPackage = row.querySelector(".tp-package").selectedOptions[0];
                    name = selectedPackage ? (selectedPackage.dataset.name || "").trim() : "";
                }
            } else if (!name && !gitUrl) {
                return;
            }
            if (!name) {
                errors.push("请填写第三方模块名称");
                return;
            }
            if (!/^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$/.test(name)) {
                errors.push("模块名只允许字母、数字、点、下划线、加号和连字符");
                return;
            }
            if (names.has(name.toLowerCase())) {
                errors.push("第三方模块名称不能重复：" + name);
                return;
            }
            names.add(name.toLowerCase());
            if (source === "git") {
                var branch = row.querySelector(".tp-branch").value.trim() || "master";
                if (!gitUrl) errors.push("模块 " + name + " 缺少 Git 仓库 URL");
                else result.push({ name: name, source: "git", git_url: gitUrl, branch: branch });
            } else {
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
        if (currentMismatchSignature && acknowledgedMismatchSignature !== currentMismatchSignature) {
            showParameterMismatchModal(selectedNodes());
            return;
        }
        var thirdParty = collectThirdParty();
        if (thirdParty.errors.length) {
            notify(thirdParty.errors[0], "warning");
            return;
        }
        var selected = selectedNodes();
        var pkg = selectedPackage();
        if (!selected.length || !pkg) {
            notify("请选择目标节点和源码包", "warning");
            goToStep(1);
            return;
        }
        var environmentProblem = environmentError();
        if (environmentProblem) {
            notify(environmentProblem, "warning");
            goToStep(2);
            return;
        }
        if (!environmentComplete) {
            notify("请先完成并确认编译环境", "warning");
            goToStep(2);
            return;
        }
        var baselinesReady = selected.every(function (node) {
            return window.upgradeBaselines[node.id] && window.upgradeBaselines[node.id].ok;
        });
        if (!baselinesReady) {
            notify("请先成功读取所有目标节点的 nginx -V 参数", "warning");
            return;
        }
        var changes = collectModuleChanges();
        var requestVersion = ++previewRequestVersion;
        previewReady = false;
        targetConfigPreviews = {};
        refreshStepperAccess();
        var button = document.getElementById("stepThreeNext");
        button.disabled = true;
        var jobs = selected.map(function (node) {
            var baseline = window.upgradeBaselines[node.id].data;
            return requestJson("POST", "/api/upgrade/compute-config", {
                current_params: baseline.params,
                added_modules: changes.added,
                removed_modules: changes.removed,
                added_third_party: thirdParty.items,
                remote_work_dir: currentWorkDir(),
                upgrade_mode: currentMode(),
                target_prefix: document.getElementById("targetPrefix").value.trim()
            }).then(function (data) {
                targetConfigPreviews[node.id] = data.target_params;
            });
        });
        $.when.apply($, jobs).done(function () {
            if (requestVersion !== previewRequestVersion) return;
            button.disabled = false;
            previewReady = true;
            renderConfirm(selected, pkg, thirdParty.items, changes);
            refreshStepperAccess();
            activateStep(4);
        }).fail(function (error) {
            if (requestVersion !== previewRequestVersion) return;
            button.disabled = false;
            notify(errorMessage(error), "danger");
        });
    }

    function formatConfigurePreview(options) {
        var value;
        if (Array.isArray(options)) {
            value = options.map(formatConfigureArgument).join(" \\\n    ");
        } else {
            value = String(options || "").trim();
        }
        return value ? "./configure \\\n    " + value : "./configure";
    }

    function formatConfigureArgument(argument) {
        var token = String(argument || "");
        var separator = token.indexOf("=");
        if (separator < 0) return quoteConfigureValue(token);
        return token.slice(0, separator + 1) + quoteConfigureValue(token.slice(separator + 1));
    }

    function quoteConfigureValue(value) {
        if (/^[A-Za-z0-9_./:+,@%-]*$/.test(value)) return value;
        return "'" + value.replace(/'/g, "'\\''") + "'";
    }

    function appendSummaryDetailGroup(container, title, values, kind) {
        var group = document.createElement("section");
        group.className = "upgrade-summary-detail-group";
        var heading = document.createElement("h4");
        heading.className = "upgrade-summary-detail-title";
        heading.textContent = title;
        var list = document.createElement("ul");
        list.className = "upgrade-summary-detail-list";
        if (!values.length) {
            var empty = document.createElement("li");
            empty.className = "small text-muted";
            empty.textContent = "无";
            list.appendChild(empty);
        }
        values.forEach(function (value) {
            var item = document.createElement("li");
            item.className = "upgrade-summary-detail-item is-" + kind;
            var marker = document.createElement("span");
            marker.setAttribute("aria-hidden", "true");
            marker.textContent = kind === "removed" ? "−" : "+";
            var code = document.createElement("code");
            code.textContent = value;
            if (kind === "removed") {
                var deleted = document.createElement("del");
                deleted.appendChild(code);
                item.append(marker, deleted);
            } else {
                item.append(marker, code);
            }
            list.appendChild(item);
        });
        group.append(heading, list);
        container.appendChild(group);
    }

    function createBuiltinModuleDetails(changes) {
        var details = document.createElement("div");
        details.className = "upgrade-summary-detail upgrade-summary-detail-groups";
        appendSummaryDetailGroup(details, "新增内置参数", changes.added, "added");
        appendSummaryDetailGroup(details, "移除参数", changes.removed, "removed");
        return details;
    }

    function thirdPartyPackageLabel(packageId) {
        var options = Array.from(document.querySelectorAll(".tp-package option"));
        var selected = options.find(function (option) {
            return option.value && Number(option.value) === Number(packageId);
        });
        return selected ? selected.textContent.trim() : "已上传离线包";
    }

    function createThirdPartyModuleDetails(modules) {
        var details = document.createElement("div");
        details.className = "upgrade-summary-detail";
        var list = document.createElement("ul");
        list.className = "upgrade-summary-detail-list";
        modules.forEach(function (module) {
            var item = document.createElement("li");
            item.className = "upgrade-summary-detail-item";
            var name = document.createElement("code");
            name.textContent = module.name;
            var metadata = document.createElement("span");
            metadata.className = "upgrade-summary-detail-meta";
            metadata.textContent = module.source === "git"
                ? "在线 Git · 分支 " + (module.branch || "master")
                : "离线包 · " + thirdPartyPackageLabel(module.package_id);
            item.append(name, metadata);
            list.appendChild(item);
        });
        details.appendChild(list);
        return details;
    }

    function createPrefixDetails(groups) {
        var details = document.createElement("div");
        details.className = "upgrade-summary-detail";
        var list = document.createElement("ul");
        list.className = "upgrade-summary-detail-list";
        groups.forEach(function (group) {
            var item = document.createElement("li");
            item.className = "upgrade-summary-detail-item";
            var prefix = document.createElement("code");
            prefix.textContent = group.prefix;
            var nodesText = document.createElement("span");
            nodesText.className = "upgrade-summary-detail-meta";
            nodesText.textContent = group.nodes.map(function (node) {
                return node.name + " (" + node.ip + ")";
            }).join(", ");
            item.append(prefix, nodesText);
            list.appendChild(item);
        });
        details.appendChild(list);
        return details;
    }

    function targetPrefixForNode(node) {
        var params = targetConfigPreviews[node.id] || [];
        for (var index = 0; index < params.length; index += 1) {
            if (params[index] === "--prefix" && params[index + 1]) return params[index + 1];
            if (params[index].indexOf("--prefix=") === 0) return params[index].slice(9);
        }
        var baseline = window.upgradeBaselines[node.id].data;
        if (currentMode() === "switch_path") {
            return document.getElementById("targetPrefix").value.trim() || baseline.prefix || "/usr/local/nginx";
        }
        return baseline.prefix || "/usr/local/nginx";
    }

    function prefixSummary(nodes) {
        var grouped = new Map();
        nodes.forEach(function (node) {
            var prefix = targetPrefixForNode(node);
            if (!grouped.has(prefix)) grouped.set(prefix, []);
            grouped.get(prefix).push(node);
        });
        var groups = Array.from(grouped, function (entry) {
            return { prefix: entry[0], nodes: entry[1] };
        });
        return {
            value: groups.length === 1
                ? groups[0].prefix
                : groups.length + " 种路径",
            details: groups.length > 1 ? createPrefixDetails(groups) : null
        };
    }

    function appendSummaryItem(grid, labelText, valueText, detail, detailId) {
        var item = document.createElement("div");
        item.className = "upgrade-summary-item";
        var label = document.createElement("span");
        label.className = "upgrade-summary-label";
        label.textContent = labelText;
        var value = document.createElement("div");
        value.className = "upgrade-summary-value";
        item.appendChild(label);
        if (!detail) {
            value.textContent = valueText;
            item.appendChild(value);
            grid.appendChild(item);
            return;
        }

        detail.id = detailId;
        detail.hidden = true;
        var toggle = document.createElement("button");
        toggle.className = "upgrade-summary-toggle";
        toggle.type = "button";
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-controls", detailId);
        toggle.title = "展开" + labelText + "详情";
        var text = document.createElement("span");
        text.textContent = valueText;
        var icon = document.createElement("i");
        icon.className = "bi bi-chevron-down";
        icon.setAttribute("aria-hidden", "true");
        toggle.append(text, icon);
        toggle.addEventListener("click", function () {
            var expanded = detail.hidden;
            detail.hidden = !expanded;
            item.classList.toggle("is-expanded", expanded);
            toggle.setAttribute("aria-expanded", String(expanded));
            toggle.title = (expanded ? "收起" : "展开") + labelText + "详情";
        });
        value.appendChild(toggle);
        item.append(value, detail);
        grid.appendChild(item);
    }

    function appendConfirmNodeRow(nodeList, node, pkg, sharedBaseline) {
        var baseline = window.upgradeBaselines[node.id].data;
        var row = document.createElement("tr");
        var hostCell = document.createElement("td");
        var hostName = document.createElement("span");
        hostName.className = "upgrade-confirm-node-hostname";
        hostName.textContent = node.name;
        hostName.title = node.name;
        var ip = document.createElement("small");
        ip.className = "text-muted upgrade-confirm-node-ip";
        ip.textContent = node.ip;
        hostCell.append(hostName, ip);
        row.appendChild(hostCell);

        [baseline.version || "未知", pkg.version || "未知"].forEach(function (version) {
            var cell = document.createElement("td");
            cell.className = "upgrade-confirm-node-version";
            cell.textContent = version;
            row.appendChild(cell);
        });

        if (!sharedBaseline) {
            var actionCell = document.createElement("td");
            var detailId = "confirmNodeConfig" + node.id;
            var detailsButton = document.createElement("button");
            detailsButton.className = "btn btn-outline-secondary btn-sm";
            detailsButton.type = "button";
            detailsButton.textContent = "查看参数";
            detailsButton.setAttribute("aria-expanded", "false");
            detailsButton.setAttribute("aria-controls", detailId);
            detailsButton.setAttribute("aria-label", "查看 " + node.name + " 的目标编译参数");
            var detailRow = document.createElement("tr");
            detailRow.className = "upgrade-confirm-node-detail-row";
            detailRow.hidden = true;
            detailRow.id = detailId;
            var detailCell = document.createElement("td");
            detailCell.colSpan = 4;
            detailRow.appendChild(detailCell);
            var rendered = false;
            detailsButton.addEventListener("click", function () {
                var expanded = detailRow.hidden;
                detailRow.hidden = !expanded;
                detailsButton.setAttribute("aria-expanded", String(expanded));
                detailsButton.textContent = expanded ? "收起参数" : "查看参数";
                if (expanded && !rendered) {
                    var output = document.createElement("pre");
                    output.className = "nginx-v-output nginx-v-output-alt upgrade-confirm-terminal upgrade-node-config-preview";
                    renderAnnotatedConfigurePreview(
                        output,
                        baseline.params || [],
                        targetConfigPreviews[node.id] || []
                    );
                    detailCell.appendChild(output);
                    rendered = true;
                }
            });
            actionCell.appendChild(detailsButton);
            row.appendChild(actionCell);
            nodeList.append(row, detailRow);
            return;
        }
        nodeList.appendChild(row);
    }

    function renderConfirm(nodes, pkg, modules, changes) {
        var summary = document.getElementById("upgradeSummary");
        summary.replaceChildren();
        var summaryGrid = document.createElement("div");
        summaryGrid.className = "upgrade-summary-grid";
        var prefix = prefixSummary(nodes);
        appendSummaryItem(summaryGrid, "节点", nodes.length + " 台");
        appendSummaryItem(summaryGrid, "源码包", pkg.name + " · " + pkg.version);
        appendSummaryItem(summaryGrid, "升级模式", currentMode() === "upgrade" ? "平滑升级（同路径）" : "切换路径升级");
        appendSummaryItem(summaryGrid, "工作目录", currentWorkDir());
        appendSummaryItem(summaryGrid, "并行编译", document.getElementById("makeJobs").value);
        appendSummaryItem(summaryGrid, "安装路径", prefix.value, prefix.details, "upgradePrefixDetails");
        appendSummaryItem(
            summaryGrid,
            "内置模块变更",
            "新增 " + changes.added.length + " 项 · 移除 " + changes.removed.length + " 项",
            changes.added.length || changes.removed.length ? createBuiltinModuleDetails(changes) : null,
            "upgradeBuiltinModuleDetails"
        );
        appendSummaryItem(
            summaryGrid,
            "第三方模块",
            modules.length + " 项",
            modules.length ? createThirdPartyModuleDetails(modules) : null,
            "upgradeThirdPartyModuleDetails"
        );
        summary.appendChild(summaryGrid);
        var baselineSignatures = nodes.map(function (node) {
            return (window.upgradeBaselines[node.id].data.params || []).slice().sort().join("\n");
        });
        var sharedBaseline = baselineSignatures.every(function (signature) {
            return signature === baselineSignatures[0];
        });
        document.getElementById("sharedConfigPreview").classList.toggle("d-none", !sharedBaseline);
        document.getElementById("perNodeConfigNote").classList.toggle("d-none", sharedBaseline);
        document.getElementById("confirmNodeTitle").textContent = "目标节点（" + nodes.length + " 台）";
        document.getElementById("confirmNodeConfigHeader").hidden = sharedBaseline;
        if (sharedBaseline) {
            renderAnnotatedConfigurePreview(
                document.getElementById("finalConfigPreview"),
                window.upgradeBaselines[nodes[0].id].data.params || [],
                targetConfigPreviews[nodes[0].id] || []
            );
        }
        var nodeList = document.getElementById("confirmNodeList");
        nodeList.replaceChildren();
        nodes.forEach(function (node) {
            appendConfirmNodeRow(nodeList, node, pkg, sharedBaseline);
        });
        document.getElementById("confirmUpgrade").checked = false;
        var startButton = document.getElementById("startUpgrade");
        if (startButton) startButton.disabled = true;
    }

    function renderAnnotatedConfigurePreview(container, baselineParams, targetParams) {
        var baseline = Array.isArray(baselineParams) ? baselineParams : [];
        var target = Array.isArray(targetParams) ? targetParams : baseline;
        var targetSet = new Set(target);
        var baselineSet = new Set(baseline);

        container.replaceChildren();
        var command = document.createElement("span");
        command.className = "upgrade-confirm-terminal-line";
        command.textContent = "./configure \\";
        container.appendChild(command);
        var entries = [];
        baseline.forEach(function (parameter) {
            entries.push({
                parameter: parameter,
                kind: targetSet.has(parameter) ? "same" : "removed"
            });
        });
        target.forEach(function (parameter) {
            if (!baselineSet.has(parameter)) entries.push({ parameter: parameter, kind: "added" });
        });
        if (!entries.length) entries.push({ parameter: "(无参数)", kind: "same" });
        entries.forEach(function (entry, index) {
            appendConfigurePreviewLine(
                container,
                entry.parameter,
                entry.kind,
                index < entries.length - 1
            );
        });
    }

    function appendConfigurePreviewLine(container, parameter, kind, continuation) {
        var line = document.createElement("span");
        line.className = "upgrade-confirm-terminal-line";
        if (kind === "same") {
            line.textContent = "    " + formatConfigureArgument(parameter);
        } else {
            var change = document.createElement(kind === "removed" ? "del" : "span");
            change.className = kind === "removed" ? "upgrade-param-removed" : "upgrade-param-added";
            change.textContent = (kind === "removed" ? "    - " : "    + ") + formatConfigureArgument(parameter);
            line.appendChild(change);
        }
        if (continuation) line.appendChild(document.createTextNode(" \\"));
        container.appendChild(line);
    }

    function createUpgrade() {
        var pkg = selectedPackage();
        if (!pkg || !stepOneComplete() || !environmentComplete || !previewReady) {
            notify("请按顺序完成升级配置和参数预览", "warning");
            return;
        }
        if (!document.getElementById("confirmUpgrade").checked) {
            notify("请确认升级节点和编译参数", "warning");
            return;
        }
        var changes = collectModuleChanges();
        var thirdParty = collectThirdParty();
        if (thirdParty.errors.length) {
            notify(thirdParty.errors[0], "warning");
            return;
        }
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
                beginBatchPolling(result.task_ids, result.batch_number, result.message);
            })
            .fail(function (error) {
                document.getElementById("startUpgrade").disabled = false;
                notify(errorMessage(error), "danger");
            });
    }

    function beginBatchPolling(taskIds, batchNumber, message) {
        var region = document.getElementById("progressLive");
        region.classList.remove("d-none");
        document.getElementById("progressPlaceholder").classList.add("d-none");
        document.getElementById("batchProgressBadge").classList.remove("d-none");
        document.getElementById("batchNumberLabel").textContent = "批次 " + batchNumber;
        var states = {};
        taskIds.forEach(function (id) { states[id] = { after: 0, status: "pending", progress: 0, detail: "等待执行" }; });
        notify(message || ("升级任务已创建，批次 " + batchNumber), "success", {
            href: "/tasks/" + taskIds[0] + "/",
            label: "查看任务详情",
            target: "_blank"
        });
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
            if (!done) {
                window.setTimeout(function () { pollBatch(taskIds, states, batchNumber); }, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            } else if (!states._completionNotified) {
                states._completionNotified = true;
                var counts = {success: 0, failed: 0, cancelled: 0};
                taskIds.forEach(function (id) { counts[states[id].status] += 1; });
                var resultType = counts.failed ? "warning" : (counts.cancelled ? "info" : "success");
                var targetId = taskIds.find(function (id) { return states[id].status === "failed"; }) || taskIds[0];
                notify(
                    "批次 " + batchNumber + " 已结束：成功 " + counts.success + "，失败 " + counts.failed + "，取消 " + counts.cancelled,
                    resultType,
                    {href: "/tasks/" + targetId + "/", label: "查看任务详情", target: "_blank"}
                );
            }
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
        bindSelectorTable("nodeSearch", ".upgrade-node-row", "node");
        bindSelectorTable("packageSearch", ".upgrade-package-row", "package");
        document.querySelectorAll(".upgrade-node-check, .upgrade-package-check").forEach(function (input) {
            input.addEventListener("change", updateSelection);
        });
        updateSelection();
        document.getElementById("stepOneNext").addEventListener("click", function () {
            if (!stepOneComplete()) {
                notify("请先选择至少一个目标节点和源码包", "warning");
                return;
            }
            activateStep(2);
        });
        document.getElementById("stepTwoNext").addEventListener("click", function () {
            var problem = environmentError();
            if (problem) {
                notify(problem, "warning");
                return;
            }
            environmentComplete = true;
            previewReady = false;
            refreshStepperAccess();
            if (currentMode() === "switch_path") {
                document.getElementById("targetPrefixWrap").classList.remove("d-none");
            }
            goToStep(3);
        });
        ["remoteWorkDir", "makeJobs", "targetPrefix"].forEach(function (id) {
            var field = document.getElementById(id);
            field.addEventListener("input", function () {
                environmentComplete = false;
                invalidateStepThreePreview();
                refreshStepperAccess();
            });
            field.addEventListener("change", function () {
                environmentComplete = false;
                invalidateStepThreePreview();
                refreshStepperAccess();
            });
        });
        document.querySelectorAll("input[name='upgradeMode']").forEach(function (input) {
            input.addEventListener("change", function () {
                document.getElementById("targetPrefixWrap").classList.toggle("d-none", currentMode() !== "switch_path");
                environmentComplete = false;
                invalidateStepThreePreview();
                refreshStepperAccess();
            });
        });
        document.getElementById("refreshNginxV").addEventListener("click", function () {
            invalidateStepThreePreview();
            fetchConfigureBaselines(true);
        });
        document.getElementById("paramsMismatchBack").addEventListener("click", function () {
            if (mismatchModal) mismatchModal.hide();
            activateStep(1);
        });
        document.getElementById("paramsMismatchContinue").addEventListener("click", function () {
            acknowledgedMismatchSignature = currentMismatchSignature;
            if (mismatchModal) mismatchModal.hide();
            invalidateStepThreePreview();
        });
        document.getElementById("referenceNode").addEventListener("change", function () {
            referenceNodeId = this.value;
            reconcileModuleChanges((referenceBaseline() || {}).params || []);
            renderReferenceBaseline();
            updateModuleSummary(true);
        });
        document.getElementById("openModuleAdjust").addEventListener("click", openModuleAdjust);
        document.getElementById("confirmModuleAdjust").addEventListener("click", confirmModuleAdjust);
        ["addModuleSearch", "removeModuleSearch"].forEach(function (inputId) {
            var input = document.getElementById(inputId);
            $(input).on("querytags:change", filterModuleOptions);
            input.addEventListener("keydown", function (event) {
                if (event.key !== "Enter" || event.isComposing) return;
                event.preventDefault();
                window.commitQueryTagValue("#" + inputId);
                filterModuleOptions();
            });
        });
        document.getElementById("addThirdParty").addEventListener("click", addThirdPartyRow);
        document.getElementById("thirdPartyRows").addEventListener("input", invalidateStepThreePreview);
        document.getElementById("thirdPartyRows").addEventListener("change", invalidateStepThreePreview);
        document.getElementById("stepThreeNext").addEventListener("click", previewUpgrade);
        document.getElementById("confirmUpgrade").addEventListener("change", function () {
            var startButton = document.getElementById("startUpgrade");
            if (startButton) startButton.disabled = !this.checked;
        });
        var startButton = document.getElementById("startUpgrade");
        if (startButton) startButton.addEventListener("click", createUpgrade);
        addThirdPartyRow();
        document.querySelectorAll("[data-step-target]").forEach(function (button) {
            button.addEventListener("click", function () {
                var target = Number(button.dataset.stepTarget);
                goToStep(target);
            });
        });
        document.querySelectorAll(".upgrade-step").forEach(function (button) {
            button.addEventListener("click", function () {
                goToStep(Number(button.dataset.stepTarget));
            });
        });
        refreshStepperAccess();
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

    function updateDetailActions(task) {
        var canExecute = detail.dataset.canExecute === "1";
        var isRollback = detail.dataset.isRollback === "1";
        var canCancel = canExecute && !isRollback && (
            task.status === "pending" ||
            (task.status === "running" && /获取|检查 gcc|创建远程工作目录|上传 Nginx 源码包|解压 Nginx 源码包/.test(task.detail))
        );
        var cancelButton = document.getElementById("cancelUpgrade");
        if (canCancel && !cancelButton) {
            cancelButton = document.createElement("button");
            cancelButton.className = "btn btn-outline-danger btn-sm";
            cancelButton.type = "button";
            cancelButton.id = "cancelUpgrade";
            cancelButton.innerHTML = '<i class="bi bi-stop-circle me-1" aria-hidden="true"></i>取消任务';
            document.getElementById("taskStatus").parentElement.appendChild(cancelButton);
        } else if (!canCancel && cancelButton) {
            cancelButton.remove();
        }

        var canRollback = canExecute && !isRollback && detail.dataset.hasBackup === "1" &&
            detail.dataset.rolledBack !== "1" && ["success", "failed"].indexOf(task.status) >= 0;
        var rollbackButton = document.getElementById("rollbackUpgrade");
        if (canRollback && !rollbackButton) {
            rollbackButton = document.createElement("button");
            rollbackButton.className = "btn btn-warning btn-sm";
            rollbackButton.type = "button";
            rollbackButton.id = "rollbackUpgrade";
            rollbackButton.innerHTML = '<i class="bi bi-arrow-counterclockwise me-1" aria-hidden="true"></i>回滚到旧版本';
            document.getElementById("taskStatus").parentElement.appendChild(rollbackButton);
        } else if (!canRollback && rollbackButton) {
            rollbackButton.remove();
        }
    }

    function cancelCurrentUpgrade() {
        window.showConfirm("取消升级", "确认取消当前任务？", function () {
            requestJson("POST", "/api/upgrade/tasks/" + detail.dataset.taskId + "/cancel", {})
                .done(function (result) { notify(result.message, "success"); window.setTimeout(pollDetail, 400); })
                .fail(function (error) { notify(errorMessage(error), "danger"); });
        });
    }

    function rollbackCurrentUpgrade() {
        window.showConfirm("回滚 Nginx", "确认恢复升级前的二进制并重新加载 Nginx？", function () {
            requestJson("POST", "/api/upgrade/tasks/" + detail.dataset.taskId + "/rollback", {})
                .done(function (result) {
                    notify(result.message, "success", {
                        href: "/tasks/" + result.task_id + "/",
                        label: "查看回滚任务",
                        target: "_blank"
                    });
                    window.location.assign("/upgrade/tasks/" + result.task_id + "/");
                })
                .fail(function (error) { notify(errorMessage(error), "danger"); });
        });
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
                var progressBar = document.getElementById("taskProgress");
                progressBar.classList.remove("bg-success", "bg-danger", "bg-secondary");
                if (["success", "failed", "cancelled"].indexOf(task.status) >= 0) {
                    progressBar.classList.add("bg-" + statusClass);
                }
                detail.dataset.taskStatus = task.status;
                updateDetailActions(task);
                if (["pending", "running"].indexOf(task.status) >= 0) {
                    window.setTimeout(pollDetail, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                } else {
                    document.getElementById("logLiveLabel").textContent = "已完成";
                }
            })
            .fail(function () {
                document.getElementById("logLiveLabel").textContent = "连接中断，正在重试";
                window.setTimeout(pollDetail, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            });
    }

    if (detail && ["pending", "running"].indexOf(detail.dataset.taskStatus) >= 0) {
        pollDetail();
    }
    if (detail) {
        detail.addEventListener("click", function (event) {
            if (event.target.closest("#cancelUpgrade")) cancelCurrentUpgrade();
            if (event.target.closest("#rollbackUpgrade")) rollbackCurrentUpgrade();
        });
    }
})(window.jQuery, window, document);
