(function () {
    "use strict";

    function initTargetToggle(button) {
        var targets = document.querySelectorAll("[data-lifecycle-extra-target]");
        var label = button.querySelector("[data-lifecycle-target-toggle-label]");
        var icon = button.querySelector("i");
        var extraCount = targets.length;
        button.addEventListener("click", function () {
            var expanded = button.getAttribute("aria-expanded") !== "true";
            targets.forEach(function (target) { target.hidden = !expanded; });
            button.setAttribute("aria-expanded", String(expanded));
            label.textContent = expanded ? "收起节点" : "展开其余 " + extraCount + " 台";
            if (icon) {
                icon.classList.toggle("bi-chevron-down", !expanded);
                icon.classList.toggle("bi-chevron-up", expanded);
            }
        });
    }

    document.querySelectorAll("[data-lifecycle-target-toggle]").forEach(initTargetToggle);

    function statusLabel(status, operation) {
        var labels = {
            pending: "等待执行",
            running: "执行中",
            success: operation === "nginx_install" ? "安装成功" : (operation === "nginx_uninstall" ? "卸载成功" : "升级成功"),
            failed: operation === "nginx_install" ? "安装失败" : (operation === "nginx_uninstall" ? "卸载失败" : "升级失败"),
            cancelled: "已取消"
        };
        if (operation === "nginx_rollback") {
            labels.success = "回滚成功";
            labels.failed = "回滚失败";
        }
        return labels[status] || status;
    }

    function statusClass(status) {
        return status === "success" ? "success" : (status === "failed" ? "danger" : (status === "running" ? "info" : "secondary"));
    }

    function renderBatch(items, config) {
        var byId = {};
        items.forEach(function (item) { byId[String(item.id)] = item; });
        document.querySelectorAll("[data-lifecycle-task-id]").forEach(function (row) {
            var item = byId[row.dataset.lifecycleTaskId];
            if (!item) return;
            var badge = row.querySelector("[data-lifecycle-status]");
            if (badge) {
                badge.dataset.status = item.status;
                badge.className = "badge text-bg-" + statusClass(item.status);
                badge.textContent = statusLabel(item.status, config.dataset.operationType);
            }
            var progress = row.querySelector("[data-lifecycle-progress]");
            var progressBar = row.querySelector("[data-lifecycle-progress-bar]");
            if (progress && progressBar) {
                var terminal = ["success", "failed", "cancelled"].indexOf(item.status) >= 0;
                var progressValue = terminal ? 100 : item.progress;
                progress.setAttribute("aria-valuenow", String(progressValue));
                progressBar.style.width = progressValue + "%";
                progressBar.classList.remove(
                    "bg-success",
                    "bg-danger",
                    "bg-secondary",
                    "progress-bar-striped",
                    "progress-bar-animated"
                );
                if (terminal) {
                    progressBar.classList.add("bg-" + statusClass(item.status));
                } else if (item.status === "running") {
                    progressBar.classList.add("progress-bar-striped", "progress-bar-animated");
                }
            }
            var detail = row.querySelector("[data-lifecycle-detail]");
            if (detail) detail.textContent = item.detail || "-";
        });
        var success = items.filter(function (item) { return item.status === "success"; }).length;
        var failed = items.filter(function (item) { return item.status === "failed" || item.status === "cancelled"; }).length;
        var summary = document.getElementById("lifecycleResultSummary");
        if (summary) {
            summary.dataset.successCount = String(success);
            summary.dataset.failCount = String(failed);
            summary.dataset.totalCount = String(items.length);
            summary.textContent = "成功 " + success + "，失败 " + failed + "，共 " + items.length + " 台";
        }
        var started = items.map(function (item) { return item.started_at; }).filter(Boolean).sort()[0];
        var finished = items.map(function (item) { return item.finished_at; }).filter(Boolean).sort().slice(-1)[0];
        if (started && window.formatUtc8DateTime) document.getElementById("lifecycleStartedAt").textContent = window.formatUtc8DateTime(started);
        if (finished && window.formatUtc8DateTime) document.getElementById("lifecycleFinishedAt").textContent = window.formatUtc8DateTime(finished);
    }

    function pollBatch(config) {
        var active = config.dataset.batchActive === "true";
        if (!active || !config.dataset.batchUrl) return;
        fetch(config.dataset.batchUrl, { headers: { Accept: "application/json" } })
            .then(function (response) { return response.json().then(function (data) { return { response: response, data: data }; }); })
            .then(function (result) {
                if (!result.response.ok || !result.data.success) throw new Error("批次状态不可用");
                renderBatch(result.data.items || [], config);
                config.dataset.batchActive = result.data.items.some(function (item) { return item.status === "pending" || item.status === "running"; }) ? "true" : "false";
                if (config.dataset.batchActive === "true") window.setTimeout(function () { pollBatch(config); }, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            })
            .catch(function () {
                window.setTimeout(function () { pollBatch(config); }, window.NGXOPS_TASK_POLL_INTERVAL || 2000);
            });
    }

    var config = document.getElementById("lifecycleBatchConfig");
    if (config) pollBatch(config);
})();
