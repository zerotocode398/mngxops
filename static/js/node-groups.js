(function ($) {
    "use strict";

    $(function () {
        var $rows = $("[data-node-row]");
        var $search = $("[data-node-search]");
        var $selectAll = $("[data-node-select-all]");
        var $empty = $("[data-node-empty]");
        var $counts = $("[data-node-selected-count]");
        var $resultCount = $("[data-node-result-count]");
        var $selectedNodes = $("[data-selected-nodes]");
        var $pageLabel = $("[data-node-page-label]");
        var $pageSize = $("#groupNodePageSize");
        var currentPage = 1;

        if (!$rows.length) {
            return;
        }

        function updateSelection() {
            var $checked = $rows.find("[data-node-checkbox]:checked");
            var selectedCount = $checked.length;
            var $visibleChecks = $rows.filter(":visible").find("[data-node-checkbox]:not(:disabled)");
            var visibleSelected = $visibleChecks.filter(":checked").length;
            var allVisibleSelected = $visibleChecks.length > 0 && visibleSelected === $visibleChecks.length;

            $counts.text(selectedCount);
            $selectAll
                .prop("disabled", $visibleChecks.length === 0)
                .prop("checked", allVisibleSelected)
                .prop("indeterminate", visibleSelected > 0 && !allVisibleSelected);
            renderSelectedNodes($checked);
        }

        function renderSelectedNodes($checked) {
            $selectedNodes.empty();
            if (!$checked.length) {
                $selectedNodes.append($("<span>", {
                    "class": "small text-muted",
                    text: "尚未选择节点"
                }));
                return;
            }

            $checked.each(function () {
                var row = this.closest("[data-node-row]");
                var hostname = row.getAttribute("data-hostname") || "";
                var ip = row.getAttribute("data-ip") || "";
                var $chip = $("<span>", {"class": "node-group-node-chip"});
                var $label = $("<span>", {
                    "class": "node-group-node-chip-label",
                    text: hostname + " (" + ip + ")"
                });
                var $remove = $("<button>", {
                    "class": "node-group-node-chip-remove",
                    type: "button",
                    title: "移除 " + hostname,
                    "aria-label": "从节点组移除 " + hostname,
                    "data-remove-node": this.value
                }).append($("<i>", {"class": "bi bi-x", "aria-hidden": "true"}));

                $chip.append($label, $remove);
                $selectedNodes.append($chip);
            });
        }

        function filterRows(resetPage) {
            var terms = window.getQueryTagValue($search[0]).split(",").map(function (term) {
                return term.trim().toLocaleLowerCase();
            }).filter(Boolean);
            var matching = $rows.filter(function () {
                var text = this.getAttribute("data-search-text") || "";
                return terms.every(function (term) { return text.indexOf(term) >= 0; });
            });
            var pageSize = Number($pageSize.val()) || 10;
            var pageCount = Math.max(1, Math.ceil(matching.length / pageSize));
            if (resetPage) currentPage = 1;
            currentPage = Math.min(currentPage, pageCount);
            $rows.hide();
            matching.slice((currentPage - 1) * pageSize, currentPage * pageSize).show();
            $empty.prop("hidden", matching.length > 0);
            $resultCount.text(matching.length);
            $pageLabel.text("第 " + currentPage + " / " + pageCount + " 页 · 共 " + matching.length + " 项");
            $("[data-node-prev]").prop("disabled", currentPage <= 1);
            $("[data-node-next]").prop("disabled", currentPage >= pageCount);
            updateSelection();
        }

        $search.on("querytags:change", function () { filterRows(true); });
        $search.on("keydown", function (event) {
            if (event.key !== "Enter" || event.isComposing) return;
            event.preventDefault();
            window.commitQueryTagValue(this);
            filterRows(true);
        });
        $pageSize.on("change", function () { filterRows(true); });
        $("[data-node-prev]").on("click", function () { currentPage -= 1; filterRows(false); });
        $("[data-node-next]").on("click", function () { currentPage += 1; filterRows(false); });
        $selectAll.on("change", function () {
            var shouldSelect = this.checked;
            $rows.filter(":visible").find("[data-node-checkbox]:not(:disabled)").prop("checked", shouldSelect);
            updateSelection();
        });
        $rows.on("change", "[data-node-checkbox]", updateSelection);
        $selectedNodes.on("click", "[data-remove-node]", function () {
            var nodeId = this.getAttribute("data-remove-node");
            $rows.find("[data-node-checkbox]").filter(function () {
                return this.value === nodeId;
            }).prop("checked", false);
            updateSelection();
        });
        $("#nodeGroupPickerModal").on("shown.bs.modal", function () {
            if ($search[0].__ngxopsQueryTags) $search[0].__ngxopsQueryTags.clear();
            $search.val("");
            filterRows(true);
            $search.trigger("focus");
        });
        filterRows(true);
    });
}(jQuery));
