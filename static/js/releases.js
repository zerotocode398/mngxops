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
        pageSize: Number(root.getAttribute("data-page-size") || 10),
        totalPages: 1,
        total: 0,
        nodes: [],
        nodeById: {},
        bindingsByNode: {},
        bindingPageInfoByNode: {},
        bindingPageByNode: {},
        bindingPageSizeByNode: {},
        loadingBindings: {},
        selectingNode: {},
        selection: {},
        expanded: {},
        syncStatus: document.getElementById("releaseSyncStatus").value || ""
    };
    var expandedStorageKey = "ngxops_releases_expanded_nodes";
    var pollTimer = null;
    var pollTaskId = null;

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function toast(message, kind, duration, action) {
        if (typeof window.showToast === "function") {
            window.showToast(message, kind || "info", duration, action);
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

    function selectedCountForNode(nodeId) {
        return Object.keys(state.selection).filter(function (bindingId) {
            return String(state.selection[bindingId].node_id) === String(nodeId);
        }).length;
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
            var node = state.nodeById[nodeId];
            var selectedCount = selectedCountForNode(nodeId);
            var selectableCount = node ? node.selectable_bindings : 0;
            this.checked = selectableCount > 0 && selectedCount === selectableCount;
            this.indeterminate = selectedCount > 0 && selectedCount < selectableCount;
            if (state.selectingNode[nodeId]) {
                this.disabled = true;
            } else {
                this.disabled = !canPublish || !node || !node.can_publish || selectableCount === 0;
            }
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
        var autoExpand = !!(window.getQueryTagValue("#releaseSearch") || state.syncStatus);
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
            var checkboxDisabled = !canPublish || !node.can_publish || node.selectable_bindings === 0;
            var $nodeRow = $(
                '<tr class="release-node-row" data-node-row="' + id + '">' +
                '<td class="text-center"><input class="form-check-input" type="checkbox" data-node-select="' + id + '" aria-label="选择节点 ' + escapeHtml(node.hostname) + ' 下全部可发布配置"' + (checkboxDisabled ? " disabled" : "") + '></td>' +
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
                '<tr class="release-bindings-row" id="release-bindings-' + id + '"' + (expanded ? "" : " hidden") + '><td colspan="10"><div class="release-bindings-panel"><div class="small text-muted" data-binding-state="' + id + '">展开以读取绑定和版本</div><div data-binding-content="' + id + '" hidden><div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0 release-bindings-table"><thead><tr><th style="width:38px"></th><th>配置</th><th>远程路径</th><th>绑定状态</th><th>已同步版本</th><th style="width:126px">发布版本</th><th style="width:76px">预览</th></tr></thead><tbody data-binding-rows="' + id + '"></tbody></table></div><div class="release-binding-pagination d-flex flex-wrap align-items-center justify-content-between gap-2 border-top pt-2 mt-2" data-binding-pagination="' + id + '"><span class="small text-muted" data-binding-page-summary="' + id + '"></span><div class="d-flex align-items-center gap-2"><label class="small text-muted" for="releaseBindingPageSize-' + id + '">每页</label><select class="form-select form-select-sm" id="releaseBindingPageSize-' + id + '" data-binding-page-size="' + id + '" style="width:94px"><option value="10">10 条/页</option><option value="25">25 条/页</option><option value="50">50 条/页</option><option value="100">100 条/页</option></select><div class="btn-group btn-group-sm" role="group" aria-label="节点配置分页"><button class="btn btn-outline-secondary" type="button" data-binding-page-action="first" data-node-id="' + id + '" title="首页" aria-label="首页"><i class="bi bi-chevron-double-left" aria-hidden="true"></i></button><button class="btn btn-outline-secondary" type="button" data-binding-page-action="previous" data-node-id="' + id + '" title="上一页" aria-label="上一页"><i class="bi bi-chevron-left" aria-hidden="true"></i></button><span class="btn btn-outline-secondary disabled" data-binding-page-count="' + id + '"></span><button class="btn btn-outline-secondary" type="button" data-binding-page-action="next" data-node-id="' + id + '" title="下一页" aria-label="下一页"><i class="bi bi-chevron-right" aria-hidden="true"></i></button><button class="btn btn-outline-secondary" type="button" data-binding-page-action="last" data-node-id="' + id + '" title="末页" aria-label="末页"><i class="bi bi-chevron-double-right" aria-hidden="true"></i></button></div></div></div></div></div></td></tr>'
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
        var id = String(nodeId);
        var node = state.nodeById[id];
        var bindings = state.bindingsByNode[id] || [];
        var pageInfo = state.bindingPageInfoByNode[id] || {page: 1, page_size: 10, total: 0, total_pages: 1};
        var $rows = $("[data-binding-rows='" + id + "']").empty();
        var $state = $("[data-binding-state='" + id + "']");
        var $content = $("[data-binding-content='" + id + "']");
        if (!bindings.length) {
            $state.text(state.syncStatus ? "该节点没有符合筛选条件的配置" : "该节点没有可发布的绑定").prop("hidden", false);
            $content.prop("hidden", true);
            return;
        }
        $state.prop("hidden", true);
        $content.prop("hidden", false);
        $("[data-binding-page-size='" + id + "']").val(String(pageInfo.page_size));
        $("[data-binding-page-summary='" + id + "']").text(
            "共 " + pageInfo.total + " 个配置 · 第 " + pageInfo.page + " / " + pageInfo.total_pages + " 页"
        );
        $("[data-binding-page-count='" + id + "']").text(pageInfo.page + " / " + pageInfo.total_pages);
        $("[data-binding-page-action='first'][data-node-id='" + id + "'], [data-binding-page-action='previous'][data-node-id='" + id + "']")
            .prop("disabled", pageInfo.page <= 1);
        $("[data-binding-page-action='next'][data-node-id='" + id + "'], [data-binding-page-action='last'][data-node-id='" + id + "']")
            .prop("disabled", pageInfo.page >= pageInfo.total_pages);
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

    function buildPageUrl(page) {
        var params = new URLSearchParams();
        var search = window.getQueryTagValue("#releaseSearch") || "";
        if (search) params.set("search", search);
        if (state.syncStatus) params.set("sync_status", state.syncStatus);
        params.set("nginx_available", $("#releaseNginxFilter").val() || "true");
        params.set("page", String(page));
        params.set("per_page", String(state.pageSize));
        return window.location.pathname + "?" + params.toString();
    }

    function updateAddress(page, replace) {
        var nextUrl = buildPageUrl(page);
        var currentUrl = window.location.pathname + window.location.search;
        if (nextUrl !== currentUrl) {
            window.history[replace ? "replaceState" : "pushState"]({}, "", nextUrl);
        }
    }

    function renderNodePagination() {
        function pageLink(label, page, title, disabled, icon) {
            var content = icon
                ? '<i class="bi ' + icon + '" aria-hidden="true"></i><span class="visually-hidden">' + label + '</span>'
                : label;
            if (disabled) {
                return '<li class="page-item disabled"><span class="page-link" title="' + title + '">' + content + '</span></li>';
            }
            return '<li class="page-item"><a class="page-link" href="' + escapeHtml(buildPageUrl(page)) + '" data-release-page="' + page + '" title="' + title + '">' + content + '</a></li>';
        }
        var links = [
            pageLink("首页", 1, "首页", state.page <= 1, "bi-chevron-double-left"),
            pageLink("上一页", state.page - 1, "上一页", state.page <= 1, "bi-chevron-left"),
            '<li class="page-item disabled"><span class="page-link page-count">' + state.page + " / " + state.totalPages + "</span></li>",
            pageLink("下一页", state.page + 1, "下一页", state.page >= state.totalPages, "bi-chevron-right"),
            pageLink("末页", state.totalPages, "末页", state.page >= state.totalPages, "bi-chevron-double-right")
        ];
        $("#releasePaginationNav").html(
            '<ul class="pagination pagination-sm justify-content-center mb-0">' + links.join("") + "</ul>"
        );
        $("#releasePaginationSummary").text(
            "共 " + state.total + " 条，第 " + state.page + " / " + state.totalPages + " 页"
        );
        $("#releasePaginationNav").closest(".pagination-controls").toggle(state.totalPages > 1);
    }

    function loadNodes(page, replaceAddress) {
        state.page = page || 1;
        $("#releasePageInput").val(String(state.page));
        $("#releasePerPageValue").val(String(state.pageSize));
        $("#releaseSyncStatus").val(state.syncStatus);
        updateAddress(state.page, !!replaceAddress);
        $("#releaseNodeRows").html('<tr><td colspan="10" class="text-center text-muted py-4">正在加载节点…</td></tr>');
        var query = {
            search: window.getQueryTagValue("#releaseSearch") || "",
            sync_status: state.syncStatus,
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
                state.bindingPageInfoByNode = {};
                state.bindingPageByNode = {};
                state.bindingPageSizeByNode = {};
                $("#releasePageSize").val(String(state.pageSize));
                $("#releasePerPageValue").val(String(state.pageSize));
                Object.keys(response.status_counts || {}).forEach(function (key) {
                    $("[data-count='" + key + "']").text(response.status_counts[key]);
                });
                $("#releaseNodeLimit").text(
                    "最多选择 " + response.max_node_count + " 个节点、" + response.max_binding_count + " 个配置"
                );
                maxBindingCount = response.max_binding_count || maxBindingCount;
                renderNodes();
                renderNodePagination();
                $("#releasePageInput").val(String(state.page));
                $("#releasePerPageValue").val(String(state.pageSize));
                $("#releasePerPageForm input[name='search']").val(query.search);
                $("#releasePerPageForm input[name='sync_status']").val(state.syncStatus);
                $("#releasePerPageForm input[name='nginx_available']").val(query.nginx_available);
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
        if (
            state.bindingsByNode[id] &&
            state.bindingPageInfoByNode[id] &&
            state.bindingPageInfoByNode[id].page === (state.bindingPageByNode[id] || 1)
        ) {
            renderBindings(id);
            return;
        }
        if (state.loadingBindings[id]) {
            return;
        }
        loadBindingsPage(id, state.bindingPageByNode[id] || 1);
    }

    function fetchBindingsPage(nodeId, page, pageSize, search, syncStatus) {
        var query = {page: page, page_size: pageSize};
        if (search) query.search = search;
        if (syncStatus) query.sync_status = syncStatus;
        return $.getJSON(
            "/api/releases/nodes/" + encodeURIComponent(nodeId) + "/bindings",
            query
        );
    }

    function loadBindingsPage(nodeId, page, pageSize) {
        var id = String(nodeId);
        var requestedSize = pageSize || state.bindingPageSizeByNode[id] || 10;
        if (state.loadingBindings[id]) return;
        state.loadingBindings[id] = true;
        $("[data-binding-state='" + id + "']").text("正在加载绑定和版本…").prop("hidden", false);
        fetchBindingsPage(
            id,
            page,
            requestedSize,
            window.getQueryTagValue("#releaseSearch") || "",
            state.syncStatus
        )
            .done(function (response) {
                state.bindingsByNode[id] = response.bindings || [];
                state.bindingPageInfoByNode[id] = {
                    page: response.page,
                    page_size: response.page_size,
                    total: response.total,
                    total_pages: response.total_pages,
                    selectable_total: response.selectable_total
                };
                if (state.nodeById[id]) {
                    state.nodeById[id].selectable_bindings = response.selectable_total;
                }
                state.bindingPageByNode[id] = response.page;
                state.bindingPageSizeByNode[id] = response.page_size;
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
        var node = state.nodeById[id];
        if (!node) return;
        if (!checked) {
            Object.keys(state.selection).forEach(function (bindingId) {
                if (String(state.selection[bindingId].node_id) === id) {
                    delete state.selection[bindingId];
                }
            });
            if (state.bindingsByNode[id]) renderBindings(id);
            updateSelectionSummary();
            return;
        }
        if (state.selectingNode[id]) return;
        if (!canAddNode(id)) {
            toast("单次发布最多选择 " + maxNodeCount + " 个节点", "warning");
            updateSelectionSummary();
            return;
        }
        if (Object.keys(state.selection).length >= maxBindingCount) {
            toast("单次发布最多选择 " + maxBindingCount + " 个配置", "warning");
            updateSelectionSummary();
            return;
        }

        state.selectingNode[id] = true;
        toggleNode(id, true);
        updateSelectionSummary();
        var limitReached = false;

        function collectPage(page) {
            return fetchBindingsPage(id, page, 100).then(function (response) {
                (response.bindings || []).some(function (binding) {
                    if (!binding.versions.length && binding.sync_status !== "marked_deleted") {
                        return false;
                    }
                    if (state.selection[String(binding.id)]) return false;
                    if (Object.keys(state.selection).length >= maxBindingCount) {
                        limitReached = true;
                        return true;
                    }
                    state.selection[String(binding.id)] = {
                        binding_id: binding.id,
                        node_id: node.id,
                        hostname: node.hostname,
                        config_name: binding.config_name,
                        remote_path: binding.remote_path,
                        version: binding.current_version,
                        action: binding.sync_status === "marked_deleted" ? "delete" : "publish"
                    };
                    return false;
                });
                if (limitReached || page >= response.total_pages) return response;
                return collectPage(page + 1);
            });
        }

        collectPage(1)
            .done(function () {
                if (limitReached) toast("单次发布最多选择 " + maxBindingCount + " 个配置", "warning");
                if (state.bindingsByNode[id]) renderBindings(id);
                updateSelectionSummary();
            })
            .fail(function (xhr) {
                var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "读取节点配置失败";
                toast(message, "danger");
            })
            .always(function () {
                state.selectingNode[id] = false;
                updateSelectionSummary();
            });
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
            var createdTask = {
                id: response.task_id,
                status: "pending",
                detail: message
            };
            showPublishTaskToast(createdTask, "发布任务已创建，正在执行");
            trackTask(response.task_id);
        }).fail(function (xhr) {
            var message = xhr.responseJSON && xhr.responseJSON.message ? xhr.responseJSON.message : "发布任务创建失败";
            toast(message, "danger");
        }).always(function () {
            $("#releaseConfirmSubmit").prop("disabled", false);
        });
    }

    function showPublishTaskToast(task, fallback) {
        var taskId = task && (task.id || task.task_id);
        var status = task && task.status;
        var type = status === "success"
            ? "success"
            : status === "failed" ? "danger" : status === "cancelled" ? "warning" : "info";
        var action = taskId ? {
            label: "查看完整日志",
            href: "/tasks/" + encodeURIComponent(taskId) + "/",
            target: "_blank"
        } : undefined;
        toast((task && task.detail) || fallback || "发布任务已结束", type, 3000, action);
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
        var taskId = pollTaskId;
        $.getJSON("/api/tasks/" + encodeURIComponent(taskId)).done(function (task) {
            if (task.status === "success" || task.status === "failed" || task.status === "cancelled") {
                pollTaskId = null;
                if (task.status === "success") {
                    loadNodes(state.page);
                }
                showPublishTaskToast(task, "发布任务已结束");
                return;
            }
            schedulePoll();
        }).fail(function () {
            schedulePoll();
        });
    }

    function trackTask(taskId) {
        if (pollTimer) {
            window.clearTimeout(pollTimer);
        }
        pollTaskId = taskId;
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
        state.page = Number($("#releasePageInput").val()) || 1;
        loadNodes(state.page, true);

        $("#releaseFilterForm").on("submit", function (event) {
            event.preventDefault();
            state.syncStatus = $("#releaseSyncStatus").val() || "";
            loadNodes(1);
        });
        $("#nginxOnlyToggle").on("change", function () {
            $("#releaseNginxFilter").val(this.checked ? "true" : "all");
            loadNodes(1);
        });
        $("#releasePerPageForm").on("submit", function (event) {
            event.preventDefault();
            state.pageSize = Number($("#releasePageSize").val()) || 10;
            loadNodes(1);
        });
        $(document).on("click", "[data-release-page]", function (event) {
            event.preventDefault();
            loadNodes(Number(this.getAttribute("data-release-page")) || 1);
        });
        $(document).on("click", ".release-status-label", function (event) {
            event.preventDefault();
            state.syncStatus = this.getAttribute("data-status") === "all" ? "" : this.getAttribute("data-status");
            $("#releaseSyncStatus").val(state.syncStatus);
            $(".release-status-label").removeClass("active");
            $(this).addClass("active");
            loadNodes(1);
        });
        $(window).on("popstate", function () {
            window.location.reload();
        });
        $(document).on("click", "[data-node-toggle]", function () {
            toggleNode(this.getAttribute("data-node-toggle"));
        });
        $(document).on("click", ".release-node-row", function (event) {
            if ($(event.target).closest("input, button, select, a, label").length) {
                return;
            }
            toggleNode(this.getAttribute("data-node-row"));
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
        $(document).on("change", "[data-binding-page-size]", function () {
            var nodeId = this.getAttribute("data-binding-page-size");
            state.bindingPageSizeByNode[nodeId] = Number(this.value) || 10;
            loadBindingsPage(nodeId, 1, state.bindingPageSizeByNode[nodeId]);
        });
        $(document).on("click", "[data-binding-page-action]", function () {
            var nodeId = this.getAttribute("data-node-id");
            var pageInfo = state.bindingPageInfoByNode[nodeId];
            if (!pageInfo || this.disabled) return;
            var action = this.getAttribute("data-binding-page-action");
            var page = pageInfo.page;
            if (action === "first") page = 1;
            if (action === "previous") page -= 1;
            if (action === "next") page += 1;
            if (action === "last") page = pageInfo.total_pages;
            loadBindingsPage(nodeId, page);
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
