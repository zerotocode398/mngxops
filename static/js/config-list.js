(function ($, window, document) {
    "use strict";

    var expandedStorageKey = "ngxops_configs_expanded_nodes";

    function readExpandedNodes() {
        try {
            var value = window.sessionStorage.getItem(expandedStorageKey);
            var parsed = value ? JSON.parse(value) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    }

    function saveExpandedNodes() {
        var expanded = $("[data-node-toggle][aria-expanded='true']").map(function () {
            return String(this.getAttribute("data-node-toggle"));
        }).get();
        try {
            window.sessionStorage.setItem(expandedStorageKey, JSON.stringify(expanded));
        } catch (error) {}
    }

    function setNodeExpanded(nodeId, expanded) {
        var button = document.querySelector("[data-node-toggle='" + nodeId + "']");
        var row = document.getElementById("node-bindings-" + nodeId);
        if (!button || !row) {
            return;
        }
        button.setAttribute("aria-expanded", expanded ? "true" : "false");
        button.setAttribute("title", expanded ? "收起节点配置" : "展开节点配置");
        var label = button.querySelector(".visually-hidden");
        if (label) {
            label.textContent = expanded ? "收起节点配置" : "展开节点配置";
        }
        row.hidden = !expanded;
    }

    function initializeNodeExpansion() {
        var saved = readExpandedNodes();
        var page = document.querySelector("[data-config-auto-expand]");
        var autoExpand = page && page.getAttribute("data-config-auto-expand") === "true";
        $("[data-node-toggle]").each(function () {
            var nodeId = String(this.getAttribute("data-node-toggle"));
            setNodeExpanded(nodeId, autoExpand || saved.indexOf(nodeId) !== -1);
        });
        saveExpandedNodes();
    }

    $(function () {
        initializeNodeExpansion();

        $(document).on("click", "[data-node-toggle]", function () {
            var nodeId = this.getAttribute("data-node-toggle");
            setNodeExpanded(nodeId, this.getAttribute("aria-expanded") !== "true");
            saveExpandedNodes();
        });

        $("#nginxOnlyToggle").on("change", function () {
            var filter = document.getElementById("configNginxFilter");
            var form = document.getElementById("configSearchForm");
            if (!filter || !form) return;
            filter.value = this.checked ? "true" : "all";
            form.requestSubmit();
        });

        var notice = document.querySelector(".config-notice");
        if (notice && window.showToast) {
            var toastType = notice.dataset.type === "error" ? "danger" : "success";
            window.showToast(notice.dataset.message || "", toastType);
        }

        $("#configPreviewModal").on("show.bs.modal", function (event) {
            var trigger = event.relatedTarget;
            if (!trigger) {
                return;
            }
            var source = trigger.parentNode.querySelector(".config-preview-source");
            $("#configPreviewTitle").text(trigger.getAttribute("data-preview-title") || "配置预览");
            $("#configPreviewPath").text(trigger.getAttribute("data-preview-path") || "");
            $("#configPreviewContent").text(source ? source.value : "");
        });
    });
})(window.jQuery, window, document);
