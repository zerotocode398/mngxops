(function () {
    "use strict";

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

    function notify(message, type, duration, action) {
        if (window.showToast) {
            window.showToast(message, type || "info", duration || 3000, action);
        }
    }

    /** 判断监听端口是否处于 Linux 特权端口范围。 */
    function isPrivilegedListenPort(port) {
        return port > 0 && port < 1024;
    }

    function buildNodePicker() {
        var table = document.getElementById("upgradeNodeTable");
        if (!table) return;
        var maxNodes = Number(document.getElementById("installStepper").dataset.batchMax) || 3;
        var selected = {};
        var nodeRows = Array.prototype.slice.call(table.querySelectorAll(".install-node-row"));
        var packageTable = document.getElementById("installPackageTable");
        var packageRows = Array.prototype.slice.call(packageTable.querySelectorAll(".upgrade-package-row"));
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
        var pollFailureNotified = false;
        var nodeSearch = document.getElementById("nodeSearch");
        var nodePageSize = document.getElementById("nodePageSize");
        var nodePage = 1;
        var packageSearch = document.getElementById("installPackageSearch");
        var packagePageSize = document.getElementById("installPackagePageSize");
        var packagePage = 1;

        table.addEventListener("change", function (event) {
            var input = event.target.closest(".upgrade-node-check");
            if (!input) return;
            var nodeId = input.value;
            if (input.checked && !selected[nodeId] && Object.keys(selected).length >= maxNodes) {
                input.checked = false;
                notify("单次最多选择 " + maxNodes + " 台节点", "warning");
                return;
            }
            if (input.checked) {
                selected[nodeId] = true;
            } else {
                delete selected[nodeId];
            }
            updateNodeCount();
        });

        nodeRows.forEach(function (row) {
            row.addEventListener("click", function (event) {
                if (event.target.closest("input, button, a, label, select")) return;
                var checkbox = row.querySelector(".upgrade-node-check");
                if (!checkbox || (checkbox.disabled && !checkbox.checked)) return;
                checkbox.checked = !checkbox.checked;
                checkbox.dispatchEvent(new Event("change", { bubbles: true }));
            });
        });

        nodeSearch.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") return;
            event.preventDefault();
            window.commitQueryTagValue("#nodeSearch");
            nodePage = 1;
            renderNodePage();
        });
        window.jQuery(nodeSearch).on("querytags:change", function () {
            nodePage = 1;
            renderNodePage();
        });
        nodePageSize.addEventListener("change", function () {
            nodePage = 1;
            renderNodePage();
        });
        document.getElementById("nodePageFirst").addEventListener("click", function () {
            nodePage = 1;
            renderNodePage();
        });
        document.getElementById("nodePagePrevious").addEventListener("click", function () {
            nodePage = Math.max(1, nodePage - 1);
            renderNodePage();
        });
        document.getElementById("nodePageNext").addEventListener("click", function () {
            nodePage += 1;
            renderNodePage();
        });
        document.getElementById("nodePageLast").addEventListener("click", function () {
            nodePage = Math.max(1, Math.ceil(matchingNodes().length / (Number(nodePageSize.value) || 10)));
            renderNodePage();
        });

        packageTable.addEventListener("change", function (event) {
            if (!event.target.matches(".upgrade-package-check")) return;
            updateNodeCount();
        });
        packageRows.forEach(function (row) {
            row.addEventListener("click", function (event) {
                if (event.target.closest("input, button, a, label, select")) return;
                var radio = row.querySelector(".upgrade-package-check");
                if (!radio || radio.disabled) return;
                radio.checked = true;
                radio.dispatchEvent(new Event("change", { bubbles: true }));
            });
        });
        packageSearch.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") return;
            event.preventDefault();
            window.commitQueryTagValue("#installPackageSearch");
            packagePage = 1;
            renderPackagePage();
        });
        window.jQuery(packageSearch).on("querytags:change", function () {
            packagePage = 1;
            renderPackagePage();
        });
        packagePageSize.addEventListener("change", function () {
            packagePage = 1;
            renderPackagePage();
        });
        document.getElementById("installPackagePageFirst").addEventListener("click", function () {
            packagePage = 1;
            renderPackagePage();
        });
        document.getElementById("installPackagePagePrevious").addEventListener("click", function () {
            packagePage = Math.max(1, packagePage - 1);
            renderPackagePage();
        });
        document.getElementById("installPackagePageNext").addEventListener("click", function () {
            packagePage += 1;
            renderPackagePage();
        });
        document.getElementById("installPackagePageLast").addEventListener("click", function () {
            packagePage = Math.max(1, Math.ceil(matchingPackages().length / (Number(packagePageSize.value) || 10)));
            renderPackagePage();
        });

        function matchingNodes() {
            var terms = window.getQueryTagValue("#nodeSearch")
                .split(/[,，]/).map(function (term) { return term.trim().toLocaleLowerCase(); })
                .filter(Boolean);
            return nodeRows.filter(function (row) {
                var text = (row.dataset.search || "").toLocaleLowerCase();
                return terms.every(function (term) { return text.indexOf(term) >= 0; });
            });
        }

        function renderNodePage() {
            var matching = matchingNodes();
            var pageSize = Number(nodePageSize.value) || 10;
            var pages = Math.max(1, Math.ceil(matching.length / pageSize));
            nodePage = Math.min(nodePage, pages);
            var start = (nodePage - 1) * pageSize;
            nodeRows.forEach(function (row) { row.classList.add("d-none"); });
            matching.slice(start, start + pageSize).forEach(function (row) {
                row.classList.remove("d-none");
            });
            var emptyRow = document.getElementById("installNodeEmptyRow");
            emptyRow.classList.toggle("d-none", matching.length > 0);
            document.getElementById("installNodeEmptyMessage").textContent = nodeRows.length
                ? "没有匹配的目标节点"
                : "暂无可用于安装的节点";
            document.getElementById("nodeResultCount").textContent = "共 " + matching.length + " 条";
            document.getElementById("nodePageLabel").textContent = nodePage + " / " + pages;
            document.getElementById("nodePageFirst").disabled = nodePage <= 1;
            document.getElementById("nodePagePrevious").disabled = nodePage <= 1;
            document.getElementById("nodePageNext").disabled = nodePage >= pages;
            document.getElementById("nodePageLast").disabled = nodePage >= pages;
            updateNodeCount();
        }

        function matchingPackages() {
            var terms = window.getQueryTagValue("#installPackageSearch")
                .split(/[,，]/).map(function (term) { return term.trim().toLocaleLowerCase(); })
                .filter(Boolean);
            return packageRows.filter(function (row) {
                var text = (row.dataset.search || "").toLocaleLowerCase();
                return terms.every(function (term) { return text.indexOf(term) >= 0; });
            });
        }

        function renderPackagePage() {
            var matching = matchingPackages();
            var pageSize = Number(packagePageSize.value) || 10;
            var pages = Math.max(1, Math.ceil(matching.length / pageSize));
            packagePage = Math.min(packagePage, pages);
            var start = (packagePage - 1) * pageSize;
            packageRows.forEach(function (row) { row.classList.add("d-none"); });
            matching.slice(start, start + pageSize).forEach(function (row) { row.classList.remove("d-none"); });
            document.getElementById("installPackageEmptyRow").classList.toggle("d-none", matching.length > 0);
            document.getElementById("installPackageEmptyMessage").textContent = packageRows.length
                ? "没有匹配的源码包"
                : "暂无源码包";
            document.getElementById("installPackageResultCount").textContent = "共 " + matching.length + " 条";
            document.getElementById("installPackagePageLabel").textContent = packagePage + " / " + pages;
            document.getElementById("installPackagePageFirst").disabled = packagePage <= 1;
            document.getElementById("installPackagePagePrevious").disabled = packagePage <= 1;
            document.getElementById("installPackagePageNext").disabled = packagePage >= pages;
            document.getElementById("installPackagePageLast").disabled = packagePage >= pages;
            updateNodeCount();
        }

        function updateNodeAvailability() {
            var selectedCount = Object.keys(selected).length;
            nodeRows.forEach(function (row) {
                var checkbox = row.querySelector(".upgrade-node-check");
                if (!checkbox) return;
                checkbox.disabled = checkbox.dataset.selectable !== "true" ||
                    (!checkbox.checked && selectedCount >= maxNodes);
            });
        }

        function renderSelectedNodeSummary() {
            var summary = document.getElementById("selectedNodeSummary");
            var nodes = selectedNodeObjects();
            summary.replaceChildren();
            if (!nodes.length) {
                summary.textContent = "尚未选择节点";
                return;
            }
            nodes.forEach(function (node) {
                var chip = document.createElement("span");
                chip.className = "upgrade-selected-chip";
                var label = document.createElement("span");
                label.textContent = (node.hostname || node.ip) + " (" + node.ip + ")";
                var remove = document.createElement("button");
                remove.type = "button";
                remove.className = "upgrade-chip-remove";
                remove.title = "移除节点";
                remove.setAttribute("aria-label", "移除节点 " + (node.hostname || node.ip));
                remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
                remove.addEventListener("click", function () {
                    var row = nodeRows.find(function (item) {
                        return item.dataset.nodeId === String(node.id);
                    });
                    var checkbox = row && row.querySelector(".upgrade-node-check");
                    if (checkbox) {
                        checkbox.checked = false;
                        checkbox.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                });
                chip.append(label, remove);
                summary.appendChild(chip);
            });
        }

        function updateNodeCount() {
            var selectedCount = Object.keys(selected).length;
            document.getElementById("stepOneCount").textContent = String(selectedCount);
            var next = document.getElementById("step1Next");
            var pkg = selectedPackage();
            next.disabled = !selectedCount || !pkg.id;
            document.getElementById("selectedPackageSummary").textContent = pkg.id
                ? "已选源码包：" + pkg.label + " · " + pkg.version
                : "尚未选择源码包";
            updateNodeAvailability();
            renderSelectedNodeSummary();
        }

        document.getElementById("step1Next").addEventListener("click", function () {
            if (!Object.keys(selected).length || !selectedPackage().id) {
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
        renderPackagePage();
        renderNodePage();
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
                label.className = "upgrade-module-option";
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
                picker.querySelectorAll(".upgrade-module-option").forEach(function (row) {
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
                row.className = "upgrade-module-option install-module-selected";
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
                ? chosenModules.join("\n")
                : "未选择编译参数";
        }

        function buildThirdPartyEditor() {
            var container = document.getElementById("thirdPartyModules");
            document.getElementById("addThirdParty").addEventListener("click", function () {
                container.appendChild(createThirdPartyRow());
            });
        }

        function createThirdPartyRow() {
            var row = document.createElement("div");
            row.className = "upgrade-third-party-row install-third-party-row";
            var grid = document.createElement("div");
            grid.className = "row g-2 align-items-end";
            var name = document.createElement("input");
            name.className = "form-control form-control-sm";
            name.maxLength = 100;
            name.placeholder = "模块名";
            name.setAttribute("aria-label", "模块名称");
            var selectedPackageName = "";
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

            var nameColumn = document.createElement("div");
            nameColumn.className = "col-12 col-md-2";
            nameColumn.appendChild(name);
            var sourceColumn = document.createElement("div");
            sourceColumn.className = "col-12 col-md-2";
            sourceColumn.appendChild(source);
            var fields = document.createElement("div");
            fields.className = "col-12 col-md-7 install-third-party-extra";
            var fieldsGrid = document.createElement("div");
            fieldsGrid.className = "row g-1";
            var url = document.createElement("input");
            url.className = "form-control form-control-sm";
            url.placeholder = "Git 仓库地址";
            url.setAttribute("aria-label", "Git 仓库地址");
            var branch = document.createElement("input");
            branch.className = "form-control form-control-sm";
            branch.placeholder = "分支，默认 master";
            branch.value = "master";
            branch.setAttribute("aria-label", "Git 分支");
            var urlColumn = document.createElement("div");
            urlColumn.className = "col-8 install-third-party-git-fields";
            urlColumn.appendChild(url);
            var branchColumn = document.createElement("div");
            branchColumn.className = "col-4 install-third-party-git-fields";
            branchColumn.appendChild(branch);
            var packageSelect = document.createElement("select");
            packageSelect.className = "form-select form-select-sm";
            packageSelect.setAttribute("aria-label", "离线模块包");
            var placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = "选择离线模块包";
            packageSelect.appendChild(placeholder);
            modulePackages.forEach(function (item) {
                var option = document.createElement("option");
                option.value = String(item.id);
                option.textContent = item.name + (item.version ? " · " + item.version : "");
                option.dataset.name = item.name;
                packageSelect.appendChild(option);
            });
            var packageColumn = document.createElement("div");
            packageColumn.className = "col-12 install-third-party-package-fields d-none";
            packageColumn.appendChild(packageSelect);
            source.addEventListener("change", updateThirdPartyFields);
            packageSelect.addEventListener("change", function () {
                var nextName = packageSelect.selectedOptions[0]
                    ? packageSelect.selectedOptions[0].dataset.name || ""
                    : "";
                if (!name.value || name.value === selectedPackageName) {
                    name.value = nextName;
                }
                selectedPackageName = nextName;
            });
            fieldsGrid.append(urlColumn, branchColumn, packageColumn);
            fields.appendChild(fieldsGrid);
            var removeColumn = document.createElement("div");
            removeColumn.className = "col-12 col-md-1 text-end";
            removeColumn.appendChild(remove);
            grid.append(sourceColumn, nameColumn, fields, removeColumn);
            row.appendChild(grid);
            row.installFields = { name: name, source: source, url: url, branch: branch, packageId: packageSelect };
            updateThirdPartyFields.call(source);
            return row;
        }

        function updateThirdPartyFields() {
            var source = this.value;
            var row = this.closest(".install-third-party-row");
            row.querySelectorAll(".install-third-party-git-fields").forEach(function (column) {
                column.classList.toggle("d-none", source !== "git");
            });
            row.querySelector(".install-third-party-package-fields").classList.toggle(
                "d-none",
                source !== "package"
            );
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
            var input = packageTable.querySelector(".upgrade-package-check:checked");
            return {
                id: input ? Number(input.value) : 0,
                version: input ? input.dataset.version : "",
                label: input ? input.dataset.name : ""
            };
        }

        function renderConfirmation(configure, pathChecks) {
            var nodes = selectedNodeObjects();
            var pkg = selectedPackage();
            var values = formValues();
            var summary = document.getElementById("installConfirmSummary");
            summary.replaceChildren();
            var summaryGrid = document.createElement("div");
            summaryGrid.className = "upgrade-summary-grid";
            [
                ["源码包", pkg.label + " · " + pkg.version],
                ["安装路径", values.target_prefix],
                ["监听端口", String(values.listen_port)],
                ["临时安装目录", values.remote_work_dir],
                ["编译并发", String(values.make_jobs)],
                ["启动管理", "优先注册并启用 nginx.service；不可管理时使用二进制启动"]
            ].forEach(function (pair) {
                var item = document.createElement("div");
                item.className = "upgrade-summary-item";
                var label = document.createElement("span");
                label.className = "upgrade-summary-label";
                label.textContent = pair[0];
                var value = document.createElement("div");
                value.className = "upgrade-summary-value";
                value.textContent = pair[1] || "-";
                item.appendChild(label);
                item.appendChild(value);
                summaryGrid.appendChild(item);
            });
            summary.appendChild(summaryGrid);
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
                state.textContent = node.nginx_available === true ? "已检测到 Nginx" : "目标可用";
                row.appendChild(identity);
                row.appendChild(state);
                nodeList.appendChild(row);
            });
            renderPathCheckResults(pathChecks || []);
            renderWarnings(nodes, values);
        }

        function escapePathCheckHtml(value) {
            return String(value || "").replace(/[&<>\"']/g, function (character) {
                return {
                    "&": "&amp;",
                    "<": "&lt;",
                    ">": "&gt;",
                    '"': "&quot;",
                    "'": "&#39;"
                }[character];
            });
        }

        function pathPermissionCell(permission) {
            var badgeClass = permission.checked
                ? (permission.writable ? "success" : "danger")
                : "secondary";
            var label = permission.checked
                ? (permission.writable ? "可写" : "无写入权限")
                : "未检查";
            return "<div><code>" + escapePathCheckHtml(permission.path) +
                "</code><span class=\"badge text-bg-" + badgeClass + "\">" + label +
                "</span><div class=\"install-path-check-message\">" +
                escapePathCheckHtml(permission.message) + "</div></div>";
        }

        function pathCheckTableHtml(results) {
            var rows = results.map(function (item) {
                var stateClass = item.success ? "success" : "danger";
                var stateLabel = item.success ? "通过" : "未通过";
                return "<tr><td><strong>" + escapePathCheckHtml(item.hostname) +
                    "</strong><div class=\"small text-muted\">" + escapePathCheckHtml(item.ip) +
                    "</div></td><td><code>" + escapePathCheckHtml(item.username || "-") +
                    "</code></td><td>" + pathPermissionCell(item.install_path) +
                    "</td><td>" + pathPermissionCell(item.remote_work_dir) +
                    "</td><td><span class=\"badge text-bg-" + stateClass + "\">" +
                    stateLabel + "</span>" + (item.error
                        ? "<div class=\"install-path-check-message\">" +
                            escapePathCheckHtml(item.error) + "</div>"
                        : "") + "</td></tr>";
            }).join("");
            return "<div class=\"table-responsive install-risk-node-wrap\"><table class=\"table table-sm table-bordered align-middle install-path-check-table mb-0\"><thead class=\"table-light\"><tr><th>节点</th><th>SSH 用户</th><th>安装路径</th><th>远程工作目录</th><th>结果</th></tr></thead><tbody>" +
                rows + "</tbody></table></div>";
        }

        function failedPathPermissionTableHtml(results) {
            var rows = [];
            results.forEach(function (item) {
                [["安装路径", item.install_path], ["远程工作目录", item.remote_work_dir]]
                    .forEach(function (entry) {
                        var permission = entry[1] || {};
                        if (permission.checked && permission.writable) {
                            return;
                        }
                        rows.push("<tr>" + installCheckNodeCellHtml(item.hostname, item.ip) +
                            "<td><code>" + escapePathCheckHtml(item.username || "-") +
                            "</code></td><td><strong>" + entry[0] + "</strong><div><code>" +
                            escapePathCheckHtml(permission.path || "-") + "</code></div></td><td>" +
                            "<div class=\"install-check-reason\">" +
                            escapePathCheckHtml(permission.message || item.error || "写入权限检查未通过") +
                            "</div>" +
                            "</td></tr>");
                    });
            });
            return installCheckTableHtml(rows.join(""));
        }

        function installCheckNodeCellHtml(hostname, ip) {
            var safeHostname = escapePathCheckHtml(hostname);
            return "<td><strong class=\"install-check-node-name\" title=\"" + safeHostname +
                "\">" + safeHostname + "</strong><div class=\"small text-muted\">" +
                escapePathCheckHtml(ip) + "</div></td>";
        }

        function installCheckTableHtml(rows) {
            return "<div class=\"table-responsive install-risk-node-wrap\"><table class=\"table table-sm table-bordered align-middle install-check-summary-table mb-0\"><thead class=\"table-light\"><tr><th>节点</th><th>SSH 用户</th><th>未通过项目</th><th>原因</th></tr></thead><tbody>" +
                rows + "</tbody></table></div>";
        }

        function renderPathCheckResults(results) {
            var section = document.getElementById("installPathCheckSection");
            var target = document.getElementById("installPathCheckResults");
            section.classList.toggle("d-none", !results.length);
            target.innerHTML = results.length ? pathCheckTableHtml(results) : "";
        }

        function renderWarnings(nodes, values) {
            var target = document.getElementById("installRiskWarnings");
            target.replaceChildren();
            var installed = nodes.filter(function (node) { return node.nginx_available === true; });
            var nonRoot = nodes.filter(function (node) {
                return node.credential_username && node.credential_username !== "root";
            });
            var privilegedPort = isPrivilegedListenPort(values.listen_port);
            if (installed.length) {
                target.appendChild(makeWarning(
                    "已检测到 Nginx 的节点将继续安装，目标目录或 systemd unit 可能被覆盖：" +
                    installed.map(function (node) { return node.hostname; }).join("、")
                ));
            }
            if (privilegedPort && nonRoot.length) {
                target.appendChild(makeWarning(
                    "以下节点由非 root SSH 用户操作，启动 Nginx 绑定特权端口 " + values.listen_port + " 时可能因权限不足失败：" +
                    nonRoot.map(function (node) {
                        return node.hostname + "（SSH 用户 " + node.credential_username + " 对应监听端口 " + values.listen_port + "）";
                    }).join("、")
                ));
            }
        }

        function makeWarning(message) {
            var alert = document.createElement("div");
            alert.className = "alert alert-warning small mb-0";
            alert.textContent = message;
            return alert;
        }

        /** 构造可快速核对主机、SSH 用户和监听端口的安全提示内容。 */
        function buildPortRiskMessage(nodes, listenPort, pathCheckFailed) {
            var rows = nodes.map(function (node) {
                return "<tr>" + installCheckNodeCellHtml(node.hostname, node.ip) +
                    "<td><code>" + escapePathCheckHtml(node.credential_username || "-") +
                    "</code></td><td><strong>监听端口</strong><div><code>" +
                    escapePathCheckHtml(listenPort) + "</code></div></td><td>" +
                    "<div class=\"install-check-reason\">非 root SSH 用户绑定特权端口时可能因权限不足失败。</div>" +
                    "</td></tr>";
            }).join("");
            return "<p class=\"mb-3\">以下节点使用非 root SSH 用户，启动 Nginx 绑定特权端口时可能缺少权限：</p>" +
                installCheckTableHtml(rows) +
                "<div class=\"alert alert-warning py-2 small mb-0\"><i class=\"bi bi-exclamation-triangle-fill me-1\" aria-hidden=\"true\"></i>建议改用 1024 以上端口。" +
                (pathCheckFailed
                    ? "请先修复路径问题并重新检查；路径通过后仍需确认端口风险。"
                    : "确认后继续到执行参数页。") + "</div>";
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
                if (!canExecute) {
                    renderConfirmation(result.target_configure_opts, []);
                    setStep(3);
                    return;
                }
                button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>正在检查远端路径权限';
                return requestJson("/api/nginx-install/path-permissions", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        node_ids: Object.keys(selected).map(Number),
                        target_prefix: values.target_prefix,
                        remote_work_dir: values.remote_work_dir
                    })
                }).then(function (pathCheck) {
                    renderConfirmation(result.target_configure_opts, pathCheck.nodes);
                    var nodes = selectedNodeObjects();
                    var riskNodes = isPrivilegedListenPort(values.listen_port) ? nodes.filter(function (node) {
                        return node.credential_username && node.credential_username !== "root";
                    }) : [];
                    if (!pathCheck.all_passed) {
                        var message = "<p class=\"mb-2\">远端路径权限检查未通过，已停止进入确认步骤。请修复以下项目后重新检查：</p><div class=\"mt-3\">" +
                            failedPathPermissionTableHtml(pathCheck.nodes) + "</div>";
                        if (riskNodes.length) {
                            message += "<hr class=\"my-3\"><h3 class=\"h6 mb-2\">监听端口风险</h3>" +
                                buildPortRiskMessage(riskNodes, values.listen_port, true);
                        }
                        window.showAlert(
                            riskNodes.length ? "安装检查未通过" : "远端路径权限检查未通过",
                            message,
                            true,
                            "lg"
                        );
                        return;
                    }
                    if (riskNodes.length) {
                        window.showConfirm("监听端口风险提示", buildPortRiskMessage(riskNodes, values.listen_port), function () {
                            setStep(3);
                        }, true, "lg", "继续");
                        return;
                    }
                    setStep(3);
                });
            }).catch(function (error) {
                notify(error.message, "danger");
            }).finally(function () {
                button.disabled = false;
                button.innerHTML = '下一步 <i class="bi bi-chevron-right ms-1" aria-hidden="true"></i>';
            });
        }

        function installStartSummaryItemHtml(label, value) {
            return "<div class=\"upgrade-summary-item\"><span class=\"upgrade-summary-label\">" +
                escapePathCheckHtml(label) + "</span><div class=\"upgrade-summary-value\">" +
                escapePathCheckHtml(value) + "</div></div>";
        }

        function buildInstallStartConfirmation(nodes, values) {
            var pkg = selectedPackage();
            var existing = nodes.filter(function (node) { return node.nginx_available === true; });
            var nonRoot = nodes.filter(function (node) {
                return node.credential_username && node.credential_username !== "root";
            });
            var summary = [
                ["目标节点", nodes.length + " 台"],
                ["源码包", pkg.label + " · " + pkg.version],
                ["安装路径", values.target_prefix],
                ["临时安装目录", values.remote_work_dir],
                ["监听端口", String(values.listen_port)],
                ["Nginx 用户 / 用户组", values.nginx_user + " / " + values.nginx_group],
                ["编译并发", String(values.make_jobs)],
                ["启动管理", "优先尝试 nginx.service；权限不足时使用二进制启动"],
                ["安装后操作", "尝试自动同步配置"],
                ["已检测到 Nginx", existing.length + " 台"]
            ].map(function (pair) {
                return installStartSummaryItemHtml(pair[0], pair[1]);
            }).join("");
            var nodeRows = nodes.map(function (node) {
                var nginxStatus = node.nginx_available === true
                    ? '<span class="badge text-bg-warning">已检测到</span>'
                    : node.nginx_available === false
                        ? '<span class="badge text-bg-success">未检测到</span>'
                        : '<span class="badge text-bg-secondary">未知</span>';
                return "<tr><td><strong class=\"install-start-node-name\" title=\"" +
                    escapePathCheckHtml(node.hostname) + "\">" + escapePathCheckHtml(node.hostname) +
                    "</strong><div class=\"small text-muted\">" + escapePathCheckHtml(node.ip) +
                    "</div></td><td><code>" + escapePathCheckHtml(node.credential_username || "-") +
                    "</code></td><td><span class=\"badge text-bg-warning\">" +
                    escapePathCheckHtml(values.listen_port) + "</span></td><td>" + nginxStatus + "</td></tr>";
            }).join("");
            var warnings = "";
            if (nonRoot.length) {
                warnings += '<div class="alert alert-info py-2 small mb-2">非 root SSH 用户将按远端权限尝试 systemd；若无 unit 写权限且无免密 sudo，将使用 Nginx 二进制启动。</div>';
            }
            if (isPrivilegedListenPort(values.listen_port) && nonRoot.length) {
                warnings += '<div class="alert alert-warning py-2 small mb-2">监听端口 ' +
                    escapePathCheckHtml(values.listen_port) +
                    ' 属于特权端口，非 root SSH 用户可能无法绑定。</div>';
            }
            if (existing.length) {
                warnings += '<div class="alert alert-warning py-2 small mb-0">已检测到 Nginx 的节点可能覆盖目标路径或 nginx.service，请核对上方节点状态。</div>';
            }
            return '<div class="install-start-confirm"><div class="upgrade-summary mb-3"><div class="upgrade-summary-grid">' +
                summary + '</div></div><section aria-labelledby="installStartNodeTitle"><h3 class="h6 mb-2" id="installStartNodeTitle">目标节点</h3>' +
                '<div class="table-responsive install-start-node-wrap"><table class="table table-sm table-hover align-middle mb-0 install-start-node-table"><thead class="table-light"><tr><th>节点 / IP</th><th>SSH 用户</th><th>监听端口</th><th>Nginx 状态</th></tr></thead><tbody>' +
                nodeRows + '</tbody></table></div></section>' + warnings + '</div>';
        }

        function confirmStart() {
            var values = formValues();
            var nodes = selectedNodeObjects();
            window.showConfirm("开始 Nginx 安装", buildInstallStartConfirmation(nodes, values), function () {
                submitInstall(values);
            }, true, "lg", "开始安装");
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
                var firstTaskId = result.task_ids && result.task_ids.length ? result.task_ids[0] : null;
                var logAction = firstTaskId ? {
                    label: "查看完整日志",
                    href: "/nginx-install/task/" + firstTaskId + "/log/",
                    target: "_blank"
                } : null;
                notify(result.message || ("安装任务已创建，批次 " + batchNumber), "success", 8000, logAction);
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
                .then(function (batch) {
                    pollFailureNotified = false;
                    renderBatch(batch);
                })
                .catch(function (error) {
                    if (!pollFailureNotified) {
                        notify("读取安装进度失败，将自动重试：" + error.message, "danger");
                        pollFailureNotified = true;
                    }
                });
        }

        function renderBatch(batch) {
            var list = document.getElementById("installTaskList");
            document.getElementById("batchProgressPercent").classList.remove("d-none");
            document.getElementById("batchProgressPercent").textContent = batch.progress + "%";
            var batchProgressBar = document.getElementById("batchProgressBar");
            batchProgressBar.style.width = batch.progress + "%";
            batchProgressBar.parentElement.setAttribute("aria-valuenow", String(batch.progress));
            batchProgressBar.classList.remove("bg-success", "bg-warning", "bg-danger", "bg-secondary");
            if (batch.finished) {
                batchProgressBar.classList.remove("progress-bar-striped", "progress-bar-animated");
                batchProgressBar.classList.add(batch.all_success ? "bg-success" : "bg-warning");
            }
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
                    var percent = document.createElement("small");
                    percent.className = "text-muted d-block mt-1";
                    percent.dataset.role = "progress-label";
                    body.appendChild(progress);
                    body.appendChild(percent);
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
                notice.textContent = "结果：成功 " + batch.success_count + "，失败 " + batch.fail_count + "，共 " + batch.total + " 台。";
            }
        }

        function updateTaskRow(row, task) {
            row.querySelector('[data-role="identity"]').textContent = task.hostname + " · " + task.ip;
            var badge = row.querySelector('[data-role="status"]');
            badge.textContent = statusLabels[task.status] || task.status;
            badge.className = "badge text-bg-" + (task.status === "success" ? "success" : (task.status === "failed" ? "danger" : (task.status === "running" ? "info" : "secondary")));
            row.querySelector(".progress-bar").style.width = task.progress + "%";
            var progressBar = row.querySelector(".progress-bar");
            progressBar.classList.remove("bg-success", "bg-danger", "bg-secondary");
            if (task.finished) progressBar.classList.add(task.status === "success" ? "bg-success" : (task.status === "failed" ? "bg-danger" : "bg-secondary"));
            row.querySelector('[data-role="progress-label"]').textContent = task.progress + "%";
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
        var logOutput = document.getElementById("installLogOutput");
        var afterLogId = Number(logOutput.dataset.afterLogId || 0);
        var terminal = ["success", "failed", "cancelled"];
        var state = document.getElementById("installLogState");
        var cancel = document.getElementById("cancelInstallTask");
        var logFilter = document.getElementById("installLogNodeFilter");
        var timer = null;

        function applyLogFilter() {
            if (!logFilter) return;
            logOutput.querySelectorAll(".upgrade-log-line").forEach(function (line) {
                line.hidden = !!logFilter.value && line.dataset.taskId !== logFilter.value;
            });
        }

        if (logFilter) logFilter.addEventListener("change", applyLogFilter);

        function poll() {
            Promise.all([
                requestJson("/api/tasks/" + taskId + "?log_view=install_full&after_log_id=" + afterLogId),
                requestJson("/api/nginx-install/batches/" + encodeURIComponent(batch))
            ]).then(function (results) {
                var task = results[0];
                var batchTask = results[1].tasks.find(function (item) { return item.task_id === taskId; });
                update(task, batchTask);
                if (terminal.indexOf(task.status) >= 0 && !task.batch_active && !task.has_more_logs) {
                    if (timer) window.clearInterval(timer);
                    timer = null;
                    state.textContent = "任务已结束";
                } else {
                    state.textContent = task.has_more_logs ? "正在载入剩余日志" : "实时更新中";
                }
            }).catch(function () {
                state.textContent = "暂时无法读取最新状态，将自动重试";
            });
        }

        function update(task, batchTask) {
            document.getElementById("installTaskStatus").textContent = statusLabels[task.status] || task.status;
            document.getElementById("installTaskStatus").className = "badge text-bg-" + (task.status === "success" ? "success" : (task.status === "failed" ? "danger" : (task.status === "running" ? "info" : "secondary")));
            var resultNode = task.result_tree && task.result_tree.node;
            if (resultNode && resultNode.startup_mode) {
                document.getElementById("installStartupMode").textContent = resultNode.startup_mode === "systemd" ? "systemctl" : "二进制";
            }
            var progressBar = document.getElementById("installTaskProgress");
            progressBar.style.width = task.progress + "%";
            progressBar.parentElement.setAttribute("aria-valuenow", String(task.progress));
            progressBar.classList.remove("bg-success", "bg-danger", "bg-secondary");
            if (terminal.indexOf(task.status) >= 0) {
                progressBar.classList.add(task.status === "success" ? "bg-success" : (task.status === "failed" ? "bg-danger" : "bg-secondary"));
            }
            if (batchTask) {
                if (batchTask.sync_ok !== null) {
                    document.getElementById("installSyncResult").textContent = (batchTask.sync_ok ? "成功 · " : "失败 · ") + batchTask.sync_detail;
                }
                if (cancel) cancel.classList.toggle("d-none", !["pending", "checking_tools", "uploading_package", "extracting_package", "preparing_modules"].includes(batchTask.phase) || terminal.indexOf(task.status) >= 0);
            }
            task.logs.forEach(function (item) {
                var empty = logOutput.querySelector("[data-empty-log]");
                if (empty) empty.remove();
                var line = document.createElement("div");
                line.className = "upgrade-log-line log-" + item.level;
                line.dataset.taskId = item.task_id == null ? "" : String(item.task_id);
                var time = document.createElement("time");
                time.textContent = new Date(item.created_at).toLocaleString();
                var message = document.createElement("span");
                message.className = "upgrade-log-message";
                message.textContent = item.message;
                line.appendChild(time);
                line.appendChild(message);
                var atBottom = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight < 60;
                logOutput.appendChild(line);
                if (atBottom) logOutput.scrollTop = logOutput.scrollHeight;
            });
            afterLogId = task.next_log_id;
            applyLogFilter();
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
        if (document.getElementById("upgradeNodeTable")) buildNodePicker();
        buildTaskDetailPoller();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startInstallPage);
    } else {
        startInstallPage();
    }
})();
