(function ($, window, document) {
    "use strict";

    $(function () {
        var $rows = $("[data-config-node-row]");
        var $search = $("#configNodeSearch");
        var $selectVisible = $("#configNodeSelectVisible");
        var $pageSize = $("#configNodePageSize");
        var $empty = $("#configNodeNoMatches");
        var $chips = $("#configSelectedNodeChips");
        var $path = $("#configPath");
        var currentPage = 1;

        function selectedCheckboxes() {
            return $rows.find("[data-config-node-checkbox]:checked");
        }

        function updateSelection() {
            var $selected = selectedCheckboxes();
            var count = $selected.length;
            $("#configNodeSelectedCount").text(count);
            $("#configSelectedNodeCount").text("已选 " + count + " 个节点");
            $path.prop("required", count > 0);
            $chips.empty();
            $selected.each(function () {
                var row = this.closest("[data-config-node-row]");
                var hostname = row.querySelector("td:nth-child(2)").textContent.trim();
                var chip = $("<span>", {"class": "badge text-bg-light border text-dark d-inline-flex align-items-center gap-1"});
                chip.append(document.createTextNode(hostname));
                $("<button>", {
                    type: "button",
                    "class": "btn-close",
                    "aria-label": "移除节点 " + hostname,
                    title: "移除 " + hostname
                }).css({"font-size": "0.55rem"}).on("click", function () {
                    $(this).closest(".badge").remove();
                    row.querySelector("[data-config-node-checkbox]").checked = false;
                    updateSelection();
                }).appendTo(chip);
                $chips.append(chip);
            });

            var $visibleChecks = $rows.filter(":visible").find("[data-config-node-checkbox]");
            var checkedVisible = $visibleChecks.filter(":checked").length;
            $selectVisible
                .prop("disabled", $visibleChecks.length === 0)
                .prop("checked", $visibleChecks.length > 0 && checkedVisible === $visibleChecks.length)
                .prop("indeterminate", checkedVisible > 0 && checkedVisible < $visibleChecks.length);
        }

        function filterRows(resetPage) {
            var terms = window.getQueryTagValue($search[0]).split(",").map(function (term) {
                return term.trim().toLocaleLowerCase();
            }).filter(Boolean);
            var $matching = $rows.filter(function () {
                var text = this.getAttribute("data-search-text") || "";
                return terms.every(function (term) { return text.indexOf(term) >= 0; });
            });
            var pageSize = Number($pageSize.val()) || 10;
            var pageCount = Math.max(1, Math.ceil($matching.length / pageSize));
            if (resetPage) currentPage = 1;
            currentPage = Math.min(currentPage, pageCount);
            $rows.hide();
            $matching.slice((currentPage - 1) * pageSize, currentPage * pageSize).show();
            $empty.prop("hidden", $matching.length > 0);
            $("#configNodeResultCount").text($matching.length);
            $("#configNodePageLabel").text(
                "第 " + currentPage + " / " + pageCount + " 页 · 共 " + $matching.length + " 项"
            );
            $("#configNodePrev").prop("disabled", currentPage <= 1);
            $("#configNodeNext").prop("disabled", currentPage >= pageCount);
            updateSelection();
        }

        if (!$rows.length) return;

        $search.on("querytags:change", function () { filterRows(true); });
        $search.on("keydown", function (event) {
            if (event.key !== "Enter" || event.isComposing) return;
            event.preventDefault();
            window.commitQueryTagValue(this);
            filterRows(true);
        });
        $pageSize.on("change", function () { filterRows(true); });
        $("#configNodePrev").on("click", function () { currentPage -= 1; filterRows(false); });
        $("#configNodeNext").on("click", function () { currentPage += 1; filterRows(false); });
        $selectVisible.on("change", function () {
            $rows.filter(":visible").find("[data-config-node-checkbox]").prop("checked", this.checked);
            updateSelection();
        });
        $rows.on("change", "[data-config-node-checkbox]", updateSelection);
        $rows.on("click", function (event) {
            if ($(event.target).closest("a, button, input, label, select, textarea").length) return;
            var checkbox = this.querySelector("[data-config-node-checkbox]");
            checkbox.checked = !checkbox.checked;
            updateSelection();
        });
        $rows.on("keydown", function (event) {
            if (event.target !== this || (event.key !== "Enter" && event.key !== " ")) return;
            event.preventDefault();
            var checkbox = this.querySelector("[data-config-node-checkbox]");
            checkbox.checked = !checkbox.checked;
            updateSelection();
        });
        $("#configNodePicker").on("shown.bs.modal", function () {
            window.clearQueryTagValue($search[0]);
            $search.val("");
            filterRows(true);
            $search.trigger("focus");
        });

        filterRows(true);
    });
}(window.jQuery, window, document));
