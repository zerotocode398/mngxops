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

    function updateSelectedCount() {
        var selected = $(".binding-node-checkbox:checked:not(:disabled)").length;
        $("#bindingSelectedCount").text(selected);
        var visible = $(".binding-node-checkbox:visible:not(:disabled)");
        var checkedVisible = visible.filter(":checked").length;
        $("#bindingSelectVisible").prop(
            "checked",
            visible.length > 0 && checkedVisible === visible.length
        );
    }

    function updateNodeAvailability() {
        var configId = $("#bindingConfigPicker").val();
        $(".binding-node-checkbox").each(function () {
            var ids = (this.getAttribute("data-config-ids") || "").split(",");
            var alreadyBound = !!configId && ids.indexOf(String(configId)) !== -1;
            var row = this.closest("tr");
            this.disabled = !configId || alreadyBound;
            if (alreadyBound) {
                this.checked = false;
            }
            if (row) {
                row.setAttribute("data-bound", alreadyBound ? "true" : "false");
            }
        });
        updateSelectedCount();
    }

    function applyConfigDefaults() {
        var option = $("#bindingConfigPicker option:selected")[0];
        if (!option || !option.value) {
            $("#bindingPathPicker, #bindingContentPicker").val("");
            updateNodeAvailability();
            return;
        }
        $("#bindingPathPicker").val(option.getAttribute("data-default-path") || "");
        var contentId = option.getAttribute("data-default-content-id");
        var contentSource = contentId ? document.getElementById(contentId) : null;
        $("#bindingContentPicker").val(contentSource ? contentSource.value : "");
        updateNodeAvailability();
    }

    function filterPickerNodes() {
        var terms = ($("#bindingNodeSearch").val() || "")
            .toLocaleLowerCase()
            .replace(/，/g, ",")
            .split(",")
            .map(function (term) { return term.trim(); })
            .filter(Boolean);
        $("#bindingNodeRows tr").each(function () {
            if (this.id === "bindingNodeNoMatches") {
                return;
            }
            var haystack = (this.getAttribute("data-node-search") || "").toLocaleLowerCase();
            var matches = terms.every(function (term) { return haystack.indexOf(term) !== -1; });
            this.hidden = !matches;
        });
        var hasVisibleNodes = $("#bindingNodeRows tr:not(#bindingNodeNoMatches):visible").length > 0;
        $("#bindingNodeNoMatches").prop("hidden", hasVisibleNodes);
        updateSelectedCount();
    }

    $(function () {
        initializeNodeExpansion();

        $(document).on("click", "[data-node-toggle]", function () {
            var nodeId = this.getAttribute("data-node-toggle");
            setNodeExpanded(nodeId, this.getAttribute("aria-expanded") !== "true");
            saveExpandedNodes();
        });

        $("#bindingCreateModal").on("show.bs.modal", function (event) {
            var trigger = event.relatedTarget;
            if (trigger) {
                var configId = trigger.getAttribute("data-config-id") || "";
                $("#bindingConfigPicker").val(configId);
                $(".binding-node-checkbox").prop("checked", false);
                if (trigger.hasAttribute("data-config-node")) {
                    $(".binding-node-checkbox[value='" + trigger.getAttribute("data-config-node") + "']")
                        .prop("checked", true);
                }
                $("#bindingNodeSearch").val("");
                filterPickerNodes();
                applyConfigDefaults();
            }
            $("#bindingModalError").prop("hidden", true).text("");
        });

        $("#bindingConfigPicker").on("change", applyConfigDefaults);
        $("#bindingNodeSearch").on("input", filterPickerNodes);
        $("#bindingSelectVisible").on("change", function () {
            var checked = this.checked;
            $("#bindingNodeRows tr:visible .binding-node-checkbox:not(:disabled)")
                .prop("checked", checked);
            updateSelectedCount();
        });
        $(document).on("change", ".binding-node-checkbox", updateSelectedCount);
        if (window.bindModalTableRowToggle) {
            window.bindModalTableRowToggle("#bindingNodeRows", ".binding-node-checkbox");
        }

        $("#bindingCreateForm").on("submit", function (event) {
            var message = "";
            if (!$("#bindingConfigPicker").val()) {
                message = "请选择配置标签。";
            } else if (!( $(".binding-node-checkbox:checked").length )) {
                message = "请至少选择一个目标节点。";
            }
            if (message) {
                event.preventDefault();
                $("#bindingModalError").text(message).prop("hidden", false);
            }
        });

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
