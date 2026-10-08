$(function () {
    var $modal = $("#nodeDetailModal");
    var detailModal = bootstrap.Modal.getOrCreateInstance($modal[0]);
    var activeNodeId = null;
    var nodeListState = document.querySelector(".node-list-state");
    var batchMax = Number(nodeListState && nodeListState.dataset.batchMax) || 3;
    var canProbe = $modal.data("can-probe") === true || $modal.data("can-probe") === "true";
    var environmentLabels = {dev: "开发", test: "测试", prod: "生产"};

    function csrfHeaders() {
        return {"X-CSRFToken": $("meta[name='csrf-token']").attr("content") || ""};
    }

    function showRequestError(xhr, fallback) {
        var message = xhr.responseJSON && xhr.responseJSON.message;
        window.showToast(message || fallback, "danger");
    }

    function taskUrl(taskId) {
        return "/api/tasks/" + encodeURIComponent(taskId);
    }

    function pollTask(taskId, onProgress, onComplete) {
        var afterLogId = 0;
        var failures = 0;

        function poll() {
            $.getJSON(taskUrl(taskId), {after_log_id: afterLogId, log_limit: 100})
                .done(function (task) {
                    failures = 0;
                    afterLogId = task.next_log_id || afterLogId;
                    onProgress(task);
                    if (["success", "failed", "cancelled"].indexOf(task.status) !== -1) {
                        onComplete(task);
                        return;
                    }
                    window.setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                })
                .fail(function (xhr) {
                    failures += 1;
                    if (failures >= 20) {
                        onComplete(null, xhr);
                        return;
                    }
                    window.setTimeout(poll, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
                });
        }

        poll();
    }

    function queueTask(url, payload, onProgress, onComplete) {
        if (!canProbe) return;
        $.ajax({
            url: url,
            method: "POST",
            contentType: "application/json",
            headers: csrfHeaders(),
            data: JSON.stringify(payload || {})
        }).done(function (created) {
            showTaskToast(
                {id: created.task_id, status: "pending", detail: created.message},
                "任务已创建，正在执行"
            );
            if (onProgress) onProgress({id: created.task_id, status: "pending", progress: 0, detail: created.message});
            pollTask(created.task_id, onProgress || $.noop, onComplete || $.noop);
        }).fail(function (xhr) {
            showRequestError(xhr, "任务创建失败");
            if (onComplete) onComplete(null, null);
        });
    }

    function updateTaskLine(selector, task) {
        var label = task.detail || task.status;
        if (task.status === "pending" || task.status === "running") {
            label = "任务 #" + task.id + " · " + task.progress + "% · " + label;
        }
        $(selector).text(label);
    }

    function renderSystemInfo(info) {
        var labels = {
            os: "操作系统", kernel: "内核", cpu: "CPU", cpu_cores: "CPU 核数",
            memory_total: "内存总量", memory_used: "内存已用",
            disk_total: "根分区总量", disk_used: "根分区已用", uptime: "运行时间"
        };
        var $table = $("<table class='table table-sm table-borderless mb-0'></table>");
        Object.keys(labels).forEach(function (key) {
            var $row = $("<tr></tr>");
            $row.append($("<th class='text-muted fw-normal'></th>").text(labels[key]));
            $row.append($("<td class='text-break'></td>").text(info[key] || "未知"));
            $table.append($row);
        });
        $("#detailSystemInfo").empty().append($table);
    }

    function patchNodeDetail(node) {
        var $row = $("tr[data-node-id='" + node.id + "']");
        var statusLabels = {online: "在线", offline: "离线", unknown: "未知"};
        $row.find(".node-select")
            .attr("data-locked", node.is_locked ? "true" : "false")
            .trigger("change");
        var $lockedBadge = $row.find(".node-locked-badge");
        if (node.is_locked && !$lockedBadge.length) {
            $("<span class='badge text-bg-warning ms-1 node-locked-badge'>已锁定</span>")
                .insertAfter($row.find(".node-detail-open").closest("strong"));
        } else if (!node.is_locked) {
            $lockedBadge.remove();
        }
        var $sshBadge = $row.find(".node-ssh-badge");
        if ($sshBadge.length) {
            $sshBadge.attr("data-status", node.status)
                .removeClass("text-bg-success text-bg-secondary text-bg-light border text-muted")
                .addClass(node.status === "online" ? "text-bg-success" : node.status === "offline" ? "text-bg-secondary" : "text-bg-light border text-muted")
                .text(statusLabels[node.status] || "未知");
        }
        var $nginxBadge = $row.find(".node-nginx-badge");
        if ($nginxBadge.length) {
            $nginxBadge.attr("data-available", node.nginx_available === null ? "null" : String(node.nginx_available))
                .attr("data-version", node.nginx_version || "")
                .removeClass("text-bg-success text-bg-warning text-bg-light border text-muted")
                .addClass(node.nginx_available === true ? "text-bg-success" : node.nginx_available === false ? "text-bg-warning" : "text-bg-light border text-muted")
                .text(node.nginx_available === true ? (node.nginx_version || "可用") : node.nginx_available === false ? "未检测到" : "未知");
        }
        if (node.last_probe_at) {
            $row.find(".node-probe-time").text(node.last_probe_at.replace("T", " ").slice(0, 16));
        }
    }

    function refreshNodeDetail(nodeId) {
        return $.getJSON("/api/nodes/" + encodeURIComponent(nodeId)).done(function (response) {
            var node = response.node;
            patchNodeDetail(node);
            if (Number(node.id) !== Number(activeNodeId)) return;
            $("#detailHostname").text(node.hostname);
            $("#detailAddress").text(node.ip + ":" + node.port);
            $("#detailEnvironment").text(environmentLabels[node.environment] || node.environment);
            $("#detailStatus").text(({online: "在线", offline: "离线", unknown: "未知"})[node.status] || node.status);
            $("#detailCredential").text(node.credential_name + " (" + node.credential_username + ")");
            $("#detailGroups").text(node.groups.length ? node.groups.join("、") : "-");
            $("#detailDescription").text(node.description || "-");
            $("#detailProbeAt").text(node.last_probe_at ? node.last_probe_at.replace("T", " ").slice(0, 19) : "未探测");
            $("#detailNginxStatus").text(node.nginx_available === true ? "可用" : node.nginx_available === false ? "未检测到" : "未知");
            $("#detailNginxVersion").text(node.nginx_version || "-");
            $("#detailNginxPath").text(node.nginx_path || "默认");
            $("#refreshSystemInfoBtn, #detectNginxBtn").prop("disabled", !canProbe || !node.has_credential);
            if (node.has_credential && canProbe && !$modal.data("auto-queued")) {
                $modal.data("auto-queued", true);
                refreshSystemInfo();
                detectNginx();
            }
        });
    }

    function completeNodeTask(task, xhr, successMessage) {
        if (!task) {
            if (xhr) showRequestError(xhr, "任务状态读取失败");
            return;
        }
        var nodes = task.result_tree && task.result_tree.nodes || [];
        nodes.forEach(function (result) {
            refreshNodeDetail(result.node_id);
        });
        $("#nodeProbeTaskStatus").text("任务 #" + task.id + " · " + task.detail);
        showTaskToast(task, successMessage);
    }

    function showTaskToast(task, fallback) {
        var type = task.status === "success"
            ? "success"
            : task.status === "pending" || task.status === "running" ? "info" : "warning";
        window.showToast(
            task.detail || fallback,
            type,
            3000,
            {
                label: "查看完整日志",
                href: "/tasks/" + encodeURIComponent(task.id) + "/",
                target: "_blank"
            }
        );
    }

    function selectedNodeIds() {
        return $(".node-select:checked").map(function () {
            return Number(this.value);
        }).get();
    }

    function bulkNodeAction(button) {
        var action = $(button).data("action");
        var nodeIds = selectedNodeIds();
        if (!nodeIds.length || nodeIds.length > batchMax) return;
        var lockedCount = $(".node-select:checked").filter(function () {
            return this.dataset.locked === "true";
        }).length;
        if (action === "lock" && lockedCount === nodeIds.length) {
            window.showToast("所选节点已锁定，无需再次锁定", "warning");
            return;
        }
        window.showConfirm(
            "确认节点操作",
            (action === "lock" ? "锁定" : "解锁") + "选中的 " + nodeIds.length + " 个节点？",
            function () {
                $.ajax({
                    url: "/api/nodes/lock",
                    method: "POST",
                    contentType: "application/json",
                    data: JSON.stringify({action: action, node_ids: nodeIds})
                }).done(function (result) {
                    if (action === "lock") {
                        try {
                            window.sessionStorage.setItem(
                                "ngxops.nodeListToast",
                                JSON.stringify({
                                    message: result.message,
                                    type: result.count < nodeIds.length ? "warning" : "success"
                                })
                            );
                        } catch (error) {}
                        window.location.reload();
                        return;
                    }
                    nodeIds.forEach(refreshNodeDetail);
                    $("#nodeProbeTaskStatus").text(result.message);
                    showTaskToast(
                        {id: result.task_id, status: "pending", detail: result.message},
                        "解锁探测任务已创建，正在执行"
                    );
                    pollTask(result.task_id, function (task) {
                        $("#nodeProbeTaskStatus").text(
                            "任务 #" + task.id + " · " + task.progress + "% · " + task.detail
                        );
                    }, function (task, xhr) {
                        completeNodeTask(task, xhr, "解锁后 SSH/Nginx 探测完成");
                    });
                }).fail(function (xhr) {
                    showRequestError(xhr, "节点操作失败");
                });
            }
        );
    }

    function openNodeDetail(nodeId) {
        activeNodeId = Number(nodeId);
        $modal.removeData("auto-queued");
        $("#detailHostname").text("正在读取...");
        $("#detailSystemInfo").empty().append($("<span class='text-muted'></span>").text("尚未采集"));
        $("#detailNginxTask").empty();
        detailModal.show();
        refreshNodeDetail(activeNodeId).fail(function (xhr) {
            $("#detailHostname").text("加载失败");
            showRequestError(xhr, "节点详情读取失败");
        });
    }

    function refreshSystemInfo() {
        if (!activeNodeId || !canProbe) return;
        $("#detailSystemInfo").empty().append($("<span class='text-muted'></span>").text("采集任务启动中..."));
        queueTask("/api/nodes/" + activeNodeId + "/system-info", {}, function (task) {
            $("#detailSystemInfo").empty().append($("<span class='text-muted'></span>").text("任务 #" + task.id + " · " + task.progress + "% · " + task.detail));
        }, function (task, xhr) {
            if (!task) {
                $("#detailSystemInfo").text("任务状态读取失败");
                if (xhr) showRequestError(xhr, "系统信息采集失败");
                return;
            }
            var node = task.result_tree && task.result_tree.nodes && task.result_tree.nodes[0];
            if (task.status === "success" && node && node.system_info) renderSystemInfo(node.system_info);
            else $("#detailSystemInfo").text(task.detail || "系统信息采集失败");
            refreshNodeDetail(activeNodeId);
            showTaskToast(task, "系统信息采集完成");
        });
    }

    function detectNginx() {
        if (!activeNodeId || !canProbe) return;
        $("#detailNginxTask").text("检测任务启动中...");
        queueTask("/api/nodes/" + activeNodeId + "/nginx-probe", {}, function (task) {
            updateTaskLine("#detailNginxTask", task);
        }, function (task, xhr) {
            if (!task) {
                $("#detailNginxTask").text("任务状态读取失败");
                if (xhr) showRequestError(xhr, "Nginx 检测失败");
                return;
            }
            var node = task.result_tree && task.result_tree.nodes && task.result_tree.nodes[0];
            if (node) {
                $("#detailNginxStatus").text(node.nginx_available === true ? "可用" : node.nginx_available === false ? "未检测到" : "未知");
                $("#detailNginxVersion").text(node.nginx_version || "-");
            }
            refreshNodeDetail(activeNodeId);
            showTaskToast(task, "Nginx 版本检测完成");
        });
    }

    function probeNodes(nodeIds) {
        if (!nodeIds.length) return;
        var batch = nodeIds.length > 1;
        var url = batch ? "/api/nodes/probe" : "/api/nodes/" + encodeURIComponent(nodeIds[0]) + "/probe";
        var payload = batch ? {node_ids: nodeIds.map(Number)} : {};
        window.showConfirm(
            "确认 SSH 探测",
            "为选中的 " + nodeIds.length + " 个节点启动后台探测？",
            function () {
                queueTask(url, payload, function (task) {
                    $("#nodeProbeTaskStatus").text("任务 #" + task.id + " · " + task.progress + "% · " + task.detail);
                }, function (task, xhr) {
                    completeNodeTask(task, xhr, "SSH 探测完成");
                });
            }
        );
    }

    $(document).on("click", ".node-detail-open", function () {
        openNodeDetail($(this).data("node-id"));
    });
    $(document).on("click", ".node-probe", function () {
        probeNodes([Number($(this).data("node-id"))]);
    });
    $(".node-bulk-probe").on("click", function () {
        var ids = selectedNodeIds();
        if (ids.length > batchMax) {
            window.showToast("批量探测最多选择 " + batchMax + " 个节点", "warning");
            return;
        }
        probeNodes(ids);
    });
    $(".node-bulk-action").on("click", function () {
        bulkNodeAction(this);
    });
    $("#refreshSystemInfoBtn").on("click", refreshSystemInfo);
    $("#detectNginxBtn").on("click", detectNginx);
});
