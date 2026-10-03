(function ($, window, document) {
    "use strict";

    var root = document.getElementById("releaseCenter");
    if (!root) {
        return;
    }

    var canPublish = root.getAttribute("data-can-publish") === "true";
    var maxNodeCount = Number(root.getAttribute("data-max-node-count") || 3);
    var maxBindingCount = Number(root.getAttribute("data-max-binding-count") || 500);
    var state = {
        page: 1,
        pageSize: 20,
        totalPages: 1,
        total: 0,
        nodes: [],
        nodeById: {},
        bindingsByNode: {},
        loadingBindings: {},
        selection: {},
        expanded: {},
        syncStatus: "",
        groups: []
    };
    var expandedStorageKey = "ngxops_releases_expanded_nodes";
    var pollTimer = null;
    var pollTaskId = null;
    var afterLogId = 0;

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function toast(message, kind) {
        if (typeof window.showToast === "function") {
            window.showToast(message, kind || "info");
        } else {
            window.alert(message);
        }
    }

    function readExpanded() {
        try {
            var ids = JSON.parse(window.sessionStorage.getItem(expandedStorageKey) || "[]");
            return Array.isArray(ids) ? ids : [];
        } catch (error) {
            return [];
        }
    }

    function saveExpanded() {
        try {
            window.sessionStorage.setItem(
                expandedStorageKey,
                JSON.stringify(Object.keys(state.expanded).filter(function (id) {
                    return state.expanded[id];
                }))
            );
        } catch (error) {}
    }

    function selectedNodeIds() {
        var ids = {};
        Object.keys(state.selection).forEach(function (bindingId) {
            ids[state.selection[bindingId].node_id] = true;
        });
        return Object.keys(ids);
    }

    function updateSelectionSummary() {
        var count = Object.keys(state.selection).length;
        var nodeCount = selectedNodeIds().length;
        $("#releaseSelectedCount").text(count);
        $("#releaseNodeLimit").text(
            "已选 " + nodeCount + " / " + maxNodeCount + " 个节点 · 最多 " + maxBindingCount + " 个配置"
        );
        $("#releaseClearSelection").prop("disabled", count === 0);
        $("#releaseStartButton").prop("disabled", !canPublish || count === 0);
        $("#releaseConfirmSummary").text(
            "共 " + count + " 个配置，分布在 " + nodeCount + " 个节点；同节点配置校验通过后统一 reload。"
        );
        $("#releaseNodeRows [data-node-select]").each(function () {
            var nodeId = String(this.getAttribute("data-node-select"));
            var nodeBindings = state.bindingsByNode[nodeId] || [];
            var selectable = nodeBindings.filter(function (binding) {
                return state.nodeById[nodeId] && state.nodeById[nodeId].can_publish && binding.versions.length;
            });
            var checked = selectable.filter(function (binding) {
                return !!state.selection[String(binding.id)];
            }).length;
            this.checked = selectable.length > 0 && checked === selectable.length;
            this.indeterminate = checked > 0 && checked < selectable.length;
        });
    }

    function nodeStateLabel(node) {
        var label = node.status === "online" ? "在线" : (node.status === "offline" ? "离线" : "未知");
        var style = node.status === "online" ? "success" : (node.status === "offline" ? "danger" : "secondary");
        return '<span class="badge text-bg-' + style + '">' + label + "</span>";
    }

    function nginxStateLabel(node) {
        if (node.nginx_available === true) {
            return '<span class="badge text-bg-success">' + escapeHtml(node.nginx_version || "可用") + "</span>";
        }
        if (node.nginx_available === false) {
            return '<span class="badge text-bg-danger">不可用</span>';
        }
        return '<span class="badge text-bg-secondary">未探测</span>';
    }

    function renderNodes() {
        var $rows = $("#releaseNodeRows").empty();
        var autoExpand = !!($("#releaseSearch").val() || state.syncStatus);
        if (!state.nodes.length) {
            $rows.append('<tr><td colspan="10" class="text-center text-muted py-4">没有匹配的节点</td></tr>');
            updateSelectionSummary();
            return;
        }
        state.nodes.forEach(function (node) {
            var id = String(node.id);
            if (autoExpand) {
                state.expanded[id] = true;
            }
            state.nodeById[id] = node;
            var groups = node.group_names.length
                ? node.group_names.map(function (name) {
                    return '<span class="badge text-bg-light border me-1">' + escapeHtml(name) + "</span>";
                }).join("")
                : '<span class="text-muted">-</span>';
            var eligibility = node.is_locked
                ? '<span class="badge text-bg-secondary">已锁定</span>'
                : (!node.can_publish
                    ? '<span class="badge text-bg-secondary">暂不可发布</span>'
                    : '<span class="badge text-bg-success">可发布</span>');
            var checkboxDisabled = !canPublish || !node.can_publish;
            var $nodeRow = $(
                '<tr class="release-node-row" data-node-row="' + id + '">' +
                '<td class="text-center"><input class="form-check-input" type="checkbox" data-node-select="' + id + '" aria-label="选择节点 ' + escapeHtml(node.hostname) + '"' + (checkboxDisabled ? " disabled" : "") + '></td>' +
                '<td class="text-nowrap"><button type="button" class="btn btn-link btn-sm p-0 me-1 text-body" data-node-toggle="' + id + '" aria-expanded="false" aria-controls="release-bindings-' + id + '" title="展开节点配置"><i class="bi bi-chevron-right node-chevron" aria-hidden="true"></i><span class="visually-hidden">展开节点配置</span></button><span class="fw-semibold">' + escapeHtml(node.hostname) + (node.is_locked ? ' <i class="bi bi-lock-fill text-muted" title="节点已锁定" aria-label="节点已锁定"></i>' : "") + '</span></td>' +
                '<td class="font-monospace small text-nowrap">' + escapeHtml(node.ip) + ':' + escapeHtml(node.port) + '</td>' +
                '<td>' + escapeHtml({dev: "开发", test: "测试", prod: "生产"}[node.environment] || node.environment) + '</td>' +
                '<td>' + groups + '</td>' +
                '<td>' + nodeStateLabel(node) + '</td>' +
                '<td>' + nginxStateLabel(node) + '</td>' +
                '<td><span class="badge text-bg-secondary">' + node.total_bindings + '</span></td>' +
                '<td>' + (node.modified_bindings ? '<span class="badge text-bg-primary">' + node.modified_bindings + '</span>' : '<span class="text-muted">-</span>') + '</td>' +
                '<td class="text-end">' + eligibility + '</td>' +
                '</tr>'
            );
            var expanded = !!state.expanded[id];
            var $bindingsRow = $(
                '<tr class="release-bindings-row" id="release-bindings-' + id + '"' + (expanded ? "" : " hidden") + '><td colspan="10"><div class="release-bindings-panel"><div class="small text-muted" data-binding-state="' + id + '">展开以读取绑定和版本</div><div class="table-responsive" data-binding-table-wrap="' + id + '" hidden><table class="table table-sm table-hover align-middle mb-0 release-bindings-table"><thead><tr><th style="width:38px"></th><th>配置</th><th>远程路径</th><th>绑定状态</th><th>已同步版本</th><th style="width:126px">发布版本</th><th style="width:76px">预览</th></tr></thead><tbody data-binding-rows="' + id + '"></tbody></table></div></div></td></tr>'
            );
            $rows.append($nodeRow, $bindingsRow);
            if (expanded) {
                toggleNode(id, true);
            }
        });
        saveExpanded();
        updateSelectionSummary();
    }

    function renderBindings(nodeId) {
        var node = state.nodeById[nodeId];
        var bindings = state.bindingsByNode[nodeId] || [];
        var $rows = $("[data-binding-rows='" + nodeId + "']").empty();
        var $state = $("[data-binding-state='" + nodeId + "']");
        var $wrap = $("[data-binding-table-wrap='" + nodeId + "']");
        if (!bindings.length) {
            $state.text("该节点没有可发布的绑定").prop("hidden", false);
            $wrap.prop("hidden", true);
            return;
        }
        $state.prop("hidden", true);
        $wrap.prop("hidden", false);
        bindings.forEach(function (binding) {
            var hasVersions = binding.versions.length > 0;
            var selected = state.selection[String(binding.id)];
            var chosenVersion = selected ? selected.version : binding.current_version;
            var versionOptions = binding.versions.map(function (version) {
                var date = version.created_at ? version.created_at.substring(0, 10) : "";
                return '<option value="' + version.version + '" data-version-id="' + version.id + '"' + (Number(chosenVersion) === version.version ? " selected" : "") + '>V' + version.version + (date ? " · " + escapeHtml(date) : "") + '</option>';
            }).join("");
            var statusLabel = {
                not_synced: ["未同步", "primary"],
                synced: ["已同步", "success"],
                modified: ["本地已修改", "primary"],
                orphaned: ["远程已删除", "secondary"],
                failed: ["同步失败", "danger"],
                marked_deleted: ["标记删除", "danger"]
            }[binding.sync_status] || [binding.sync_status, "secondary"];
            var previewVersion = binding.versions.filter(function (version) {
                return version.version === Number(chosenVersion);
            })[0];
            var markedDeleted = binding.sync_status === "marked_deleted";
            var selectable = canPublish && node.can_publish && (hasVersions || markedDeleted);
            var versionControl = markedDeleted
                ? '<span class="badge text-bg-danger">远程删除</span>'
                : '<select class="form-select form-select-sm" data-binding-version="' + binding.id + '" aria-label="' + escapeHtml(binding.config_name) + ' 发布版本"' + (hasVersions ? "" : " disabled") + '>' + versionOptions + '</select>';
            var previewControl = markedDeleted || !previewVersion
                ? '<button class="btn btn-outline-secondary btn-sm" type="button" disabled title="待删除绑定没有待发布版本"><i class="bi bi-eye" aria-hidden="true"></i></button>'
                : '<button class="btn btn-outline-secondary btn-sm" type="button" data-version-preview="' + previewVersion.id + '" title="预览所选版本" aria-label="预览 ' + escapeHtml(binding.config_name) + '"><i class="bi bi-eye" aria-hidden="true"></i></button>';
            $rows.append(
                '<tr data-binding-row="' + binding.id + '">' +
                '<td class="text-center"><input class="form-check-input" type="checkbox" data-binding-select="' + binding.id + '" data-node-id="' + nodeId + '"' + (selected ? " checked" : "") + (selectable ? "" : ' disabled title="节点状态或版本不满足发布条件"') + ' aria-label="选择 ' + escapeHtml(binding.config_name) + '"></td>' +
                '<td class="fw-semibold">' + escapeHtml(binding.config_name) + '</td>' +
                '<td class="font-monospace small text-break">' + escapeHtml(binding.remote_path) + '</td>' +
                '<td><span class="badge text-bg-' + statusLabel[1] + '">' + escapeHtml(statusLabel[0]) + '</span></td>' +
                '<td>' + (binding.synced_version ? 'V' + binding.synced_version : '<span class="text-muted">-</span>') + '</td>' +
                '<td>' + versionControl + '</td>' +
                '<td>' + previewControl + '</td>' +
                '</tr>'
            );
        });
        updateSelectionSummary();
    }

    function loadNodes(page) {
        state.page = page || 1;
        $("#releaseNodeRows").html('<tr><td colspan="10" class="text-center text-muted py-4">正在加载节点…</td></tr>');
        var syncValue = state.syncStatus;
        var query = {
            search: $("#releaseSearch").val() || "",
            group_id: $("#releaseGroupFilter").val() || undefined,
            environment: $("#releaseEnvironmentFilter").val() || "",
            status: $("#releaseNodeStatusFilter").val() || "",
            sync_status: syncValue,
            nginx_available: $("#releaseNginxFilter").val() || "true",
            page: state.page,
            page_size: state.pageSize
        };
        $.getJSON("/api/releases/nodes", query)
            .done(function (response) {
                state.nodes = response.items || [];
                state.total = response.total || 0;
                state.page = response.page || 1;
                state.pageSize = response.page_size || state.pageSize;
                state.totalPages = response.total_pages || 1;
                state.nodeById = {};
                state.bindingsByNode = {};
                $("#releasePageSize").val(String(state.pageSize));
                Object.keys(response.status_counts || {}).forEach(function (key) {
                    $("[data-count='" + key + "']").text(response.status_counts[key]);
                });
                $("#releaseNodeLimit").text(
                    "最多选择 " + response.max_node_count + " 个节点、" + response.max_binding_count + " 个配置"
                );
                maxBindingCount = response.max_binding_count || maxBindingCount;
                renderNodes();
                $("#releasePaginationSummary").text(
                    "共 " + state.total + " 个节点 · 第 " + state.page + " / " + state.totalPages + " 页"
                );
                $("#releasePreviousPage").prop("disabled", state.page <= 1);
                $("#releaseNextPage").prop("disabled", state.page >= state.totalPages);
            })
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "节点列表加载失败";
                $("#releaseNodeRows").html('<tr><td colspan="10" class="text-center text-danger py-4">' + escapeHtml(message) + '</td></tr>');
            });
    }

    function toggleNode(nodeId, open) {
        var id = String(nodeId);
        if (open === undefined) {
            open = !state.expanded[id];
        }
        state.expanded[id] = !!open;
        var $button = $("[data-node-toggle='" + id + "']");
        $button.attr("aria-expanded", open ? "true" : "false");
        $button.attr("title", open ? "收起节点配置" : "展开节点配置");
        var $row = $("#release-bindings-" + id);
        $row.prop("hidden", !open);
        saveExpanded();
        if (!open) {
            return;
        }
        if (state.bindingsByNode[id]) {
            renderBindings(id);
            return;
        }
        if (state.loadingBindings[id]) {
            return;
        }
        state.loadingBindings[id] = true;
        $("[data-binding-state='" + id + "']").text("正在加载绑定和版本…").prop("hidden", false);
        var loadNodeId = $button.closest("tr").attr("data-node-row");
        $.getJSON("/api/releases/nodes/" + encodeURIComponent(loadNodeId) + "/bindings")
            .done(function (response) {
                state.bindingsByNode[id] = response.bindings || [];
                renderBindings(id);
            })
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "绑定加载失败";
                $("[data-binding-state='" + id + "']").text(message).prop("hidden", false);
            })
            .always(function () {
                state.loadingBindings[id] = false;
            });
    }

    function canAddNode(nodeId) {
        var ids = selectedNodeIds();
        return ids.indexOf(String(nodeId)) !== -1 || ids.length < maxNodeCount;
    }

    function setBindingSelected(bindingId, nodeId, selected) {
        var id = String(bindingId);
        var node = state.nodeById[String(nodeId)];
        var binding = (state.bindingsByNode[String(nodeId)] || []).filter(function (item) {
            return String(item.id) === id;
        })[0];
        if (!binding || !node || !node.can_publish) {
            return false;
        }
        if (selected && !state.selection[id] && Object.keys(state.selection).length >= maxBindingCount) {
            toast("单次发布最多选择 " + maxBindingCount + " 个配置", "warning");
            return false;
        }
        if (selected && !canAddNode(nodeId)) {
            toast("单次发布最多选择 " + maxNodeCount + " 个节点", "warning");
            return false;
        }
        if (selected) {
            var select = document.querySelector("[data-binding-version='" + id + "']");
            state.selection[id] = {
                binding_id: binding.id,
                node_id: node.id,
                hostname: node.hostname,
                config_name: binding.config_name,
                remote_path: binding.remote_path,
                version: select ? Number(select.value) : binding.current_version,
                action: binding.sync_status === "marked_deleted" ? "delete" : "publish"
            };
        } else {
            delete state.selection[id];
        }
        updateSelectionSummary();
        return true;
    }

    function selectAllForNode(nodeId, checked) {
        var id = String(nodeId);
        if (!state.bindingsByNode[id]) {
            toggleNode(id, true);
            var retry = setInterval(function () {
                if (!state.bindingsByNode[id] && !state.loadingBindings[id]) {
                    clearInterval(retry);
                    return;
                }
                if (state.bindingsByNode[id]) {
                    clearInterval(retry);
                    selectAllForNode(id, checked);
                }
            }, 100);
            return;
        }
        var bindings = state.bindingsByNode[id].filter(function (binding) {
            return binding.versions.length > 0 || binding.sync_status === "marked_deleted";
        });
        for (var i = 0; i < bindings.length; i += 1) {
            if (checked && !state.selection[String(bindings[i].id)] && Object.keys(state.selection).length >= maxBindingCount) {
                toast("单次发布最多选择 " + maxBindingCount + " 个配置", "warning");
                break;
            }
            var selectedNow = selectedNodeIds().indexOf(id) !== -1;
            if (checked && !selectedNow && selectedNodeIds().length >= maxNodeCount) {
                toast("单次发布最多选择 " + maxNodeCount + " 个节点", "warning");
                break;
            }
            setBindingSelected(bindings[i].id, id, checked);
        }
        renderBindings(id);
    }

    function selectedItems() {
        return Object.keys(state.selection).map(function (id) {
            return state.selection[id];
        });
    }

    function openConfirm() {
        var items = selectedItems();
        if (!items.length) {
            return;
        }
        var $rows = $("#releaseConfirmRows").empty();
        items.forEach(function (item) {
            $rows.append(
                "<tr><td>" + escapeHtml(item.hostname) + "</td><td>" +
                escapeHtml(item.config_name) + "</td><td>" + (item.action === "delete" ? "删除远程配置" : "V" + item.version) +
                "</td><td class=\"font-monospace small text-break\">" +
                escapeHtml(item.remote_path) + "</td></tr>"
            );
        });
        bootstrap.Modal.getOrCreateInstance(document.getElementById("releaseConfirmModal")).show();
    }

    function clearSelection() {
        state.selection = {};
        Object.keys(state.bindingsByNode).forEach(function (nodeId) {
            if (state.expanded[nodeId]) {
                renderBindings(nodeId);
            }
        });
        updateSelectionSummary();
    }

    function createPublish() {
        var items = selectedItems();
        $("#releaseConfirmSubmit").prop("disabled", true);
        $.ajax({
            url: "/api/releases/publish",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({
                bindings: items.map(function (item) {
                    return {binding_id: item.binding_id, version: item.version};
                })
            })
        }).done(function (response) {
            bootstrap.Modal.getOrCreateInstance(document.getElementById("releaseConfirmModal")).hide();
            clearSelection();
            var message = response.message || "发布任务已创建";
            if (response.skipped && response.skipped.length) {
                message += "；" + response.skipped.length + " 项因节点状态变化已跳过";
            }
            toast(message, "success");
            openProgress(response.task_id, response.batch_number);
        }).fail(function (xhr) {
            var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "发布任务创建失败";
            toast(message, "error");
        }).always(function () {
            $("#releaseConfirmSubmit").prop("disabled", false);
        });
    }

    function appendLogs(logs) {
        var $log = $("#releaseProgressLogs");
        logs.forEach(function (entry) {
            var line = (entry.created_at || "") + "  " + (entry.message || "") + "\n";
            $log[0].appendChild(document.createTextNode(line));
        });
        $log.scrollTop($log[0].scrollHeight);
    }

    function renderTaskTree(tree) {
        var $container = $("#releaseProgressTree").empty();
        if (!tree || !tree.nodes || !tree.nodes.length) {
            $container.append($("<span>").addClass("small text-muted").text("等待任务进度…"));
            return;
        }
        tree.nodes.forEach(function (node) {
            var $section = $("<div>").addClass("release-tree-node py-2");
            var nodeText = node.hostname + " (" + node.ip + ") · " + node.status;
            $section.append($("<div>").addClass("small fw-semibold mb-1").text(nodeText));
            var $list = $("<div>").addClass("small");
            (node.bindings || []).forEach(function (binding) {
                var operation = binding.action === "delete" ? "远程删除" : "V" + binding.version;
                var text = binding.config_name + " " + operation + " · " + binding.status + " · " + binding.message;
                $list.append($("<div>").addClass("text-break").text(text));
            });
            $section.append($list);
            $container.append($section);
        });
        if (tree.summary) {
            var summary = "成功 " + tree.summary.success + "，失败 " + tree.summary.failed + "，共 " + tree.summary.total;
            $container.prepend($("<div>").addClass("small text-muted border-bottom pb-2 mb-2").text(summary));
        }
    }

    function schedulePoll() {
        if (!pollTaskId) {
            return;
        }
        pollTimer = window.setTimeout(pollTask, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
    }

    function pollTask() {
        if (!pollTaskId) {
            return;
        }
        $.getJSON("/api/tasks/" + encodeURIComponent(pollTaskId), {
            after_log_id: afterLogId,
            log_limit: 500
        }).done(function (task) {
            $("#releaseProgressDetail").text(task.detail || "任务执行中");
            $("#releaseProgressPercent").text(task.progress + "%");
            $("#releaseProgressBar").css("width", task.progress + "%").attr("aria-valuenow", task.progress);
            if (task.status !== "pending" && task.status !== "running") {
                $("#releaseProgressBar").removeClass("progress-bar-striped progress-bar-animated");
            }
            renderTaskTree(task.result_tree);
            appendLogs(task.logs || []);
            afterLogId = task.next_log_id || afterLogId;
            if (task.status === "success" || task.status === "failed" || task.status === "cancelled") {
                $("#releaseProgressTitle").text(task.status === "success" ? "发布完成" : (task.status === "cancelled" ? "发布已取消" : "发布失败"));
                $("#releaseProgressFinal").text(task.detail || "");
                $("#releaseProgressFinal").toggleClass("text-danger", task.status !== "success").toggleClass("text-success", task.status === "success");
                pollTaskId = null;
                if (task.status === "success") {
                    loadNodes(state.page);
                }
                return;
            }
            schedulePoll();
        }).fail(function () {
            $("#releaseProgressDetail").text("任务状态暂时无法读取，正在重试");
            schedulePoll();
        });
    }

    function openProgress(taskId, batchNumber) {
        if (pollTimer) {
            window.clearTimeout(pollTimer);
        }
        pollTaskId = taskId;
        afterLogId = 0;
        $("#releaseProgressTitle").text("发布进度 · " + batchNumber);
        $("#releaseProgressDetail").text("任务已创建");
        $("#releaseProgressPercent").text("0%");
        $("#releaseProgressBar").css("width", "0%").addClass("progress-bar-striped progress-bar-animated");
        $("#releaseProgressTree").empty().append($("<span>").addClass("small text-muted").text("等待任务进度…"));
        $("#releaseProgressLogs").empty();
        $("#releaseProgressFinal").empty().removeClass("text-danger text-success");
        bootstrap.Modal.getOrCreateInstance(document.getElementById("releaseProgressModal")).show();
        pollTask();
    }

    function previewVersion(versionId, configName) {
        if (!versionId) {
            return;
        }
        $("#releaseVersionPreviewTitle").text(configName + " · 配置版本预览");
        $("#releaseVersionPreviewContent").text("正在加载…");
        bootstrap.Modal.getOrCreateInstance(document.getElementById("releaseVersionPreviewModal")).show();
        $.getJSON("/api/releases/versions/" + encodeURIComponent(versionId))
            .done(function (response) {
                $("#releaseVersionPreviewContent").text(response.content || "");
            })
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "版本内容读取失败";
                $("#releaseVersionPreviewContent").text(message);
            });
    }

    $(function () {
        readExpanded().forEach(function (id) {
            state.expanded[String(id)] = true;
        });
        loadNodes(1);

        $("#releaseFilterForm").on("submit", function (event) {
            event.preventDefault();
            loadNodes(1);
        });
        $("#releaseGroupFilter, #releaseEnvironmentFilter, #releaseNodeStatusFilter, #releaseNginxFilter").on("change", function () {
            loadNodes(1);
        });
        $("#releasePageSize").on("change", function () {
            state.pageSize = Number(this.value) || 20;
            loadNodes(1);
        });
        $("#releasePreviousPage").on("click", function () {
            if (state.page > 1) { loadNodes(state.page - 1); }
        });
        $("#releaseNextPage").on("click", function () {
            if (state.page < state.totalPages) { loadNodes(state.page + 1); }
        });
        $(document).on("click", ".release-status-filter", function () {
            $(".release-status-filter").removeClass("active");
            $(this).addClass("active");
            state.syncStatus = this.getAttribute("data-status") === "all" ? "" : this.getAttribute("data-status");
            loadNodes(1);
        });
        $(document).on("click", "[data-node-toggle]", function () {
            toggleNode(this.getAttribute("data-node-toggle"));
        });
        $(document).on("change", "[data-node-select]", function () {
            selectAllForNode(this.getAttribute("data-node-select"), this.checked);
        });
        $(document).on("change", "[data-binding-select]", function () {
            var selected = this.checked;
            if (!setBindingSelected(this.getAttribute("data-binding-select"), this.getAttribute("data-node-id"), selected)) {
                this.checked = !selected;
            }
        });
        $(document).on("click", "[data-binding-row]", function (event) {
            if ($(event.target).closest("input, select, button, a").length) {
                return;
            }
            var $checkbox = $(this).find("[data-binding-select]");
            if (!$checkbox.prop("disabled")) {
                $checkbox.prop("checked", !$checkbox.prop("checked")).trigger("change");
            }
        });
        $(document).on("change", "[data-binding-version]", function () {
            var bindingId = this.getAttribute("data-binding-version");
            var nodeId = $(this).closest("[data-binding-rows]").attr("data-binding-rows");
            if (state.selection[String(bindingId)]) {
                state.selection[String(bindingId)].version = Number(this.value);
            }
            var version = this.options[this.selectedIndex];
            $("[data-binding-row='" + bindingId + "'] [data-version-preview]").attr("data-version-preview", version ? version.getAttribute("data-version-id") : "");
            updateSelectionSummary();
        });
        $(document).on("click", "[data-version-preview]", function () {
            var bindingRow = $(this).closest("tr");
            var configName = bindingRow.find("td:nth-child(2)").text();
            previewVersion(this.getAttribute("data-version-preview"), configName);
        });
        $("#releaseClearSelection").on("click", clearSelection);
        $("#releaseStartButton").on("click", openConfirm);
        $("#releaseConfirmSubmit").on("click", createPublish);
    });
}(jQuery, window, document));
