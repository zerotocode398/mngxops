(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var picker = document.querySelector("[data-role-picker]");
        if (!picker) {
            return;
        }

        var options = Array.prototype.slice.call(
            picker.querySelectorAll("[data-role-option]")
        );
        var controls = picker.querySelectorAll("[data-role-controls]");
        var search = picker.querySelector("[data-role-search]");
        var selectedCount = document.querySelector("[data-selected-role-count]");
        var selectedFilterCount = picker.querySelector("[data-selected-filter-count]");
        var visibleCount = picker.querySelector("[data-visible-role-count]");
        var empty = picker.querySelector("[data-role-empty]");
        var selectVisible = picker.querySelector("[data-select-visible-roles]");
        var clearRoles = picker.querySelector("[data-clear-roles]");
        var filter = "all";

        function optionMatchesSearch(option, value) {
            var text = option.getAttribute("data-search-text") || "";
            return !value || text.indexOf(value) !== -1;
        }

        function renderOptions() {
            var value = (search.value || "").trim().toLocaleLowerCase();
            var matching = options.filter(function (option) {
                return optionMatchesSearch(option, value);
            });
            var selected = options.filter(function (option) {
                return option.querySelector("[data-role-checkbox]").checked;
            });
            var visible = matching.filter(function (option) {
                return filter === "all" || option.querySelector("[data-role-checkbox]").checked;
            });

            options.forEach(function (option) {
                var checkbox = option.querySelector("[data-role-checkbox]");
                option.hidden = visible.indexOf(option) === -1;
                option.classList.toggle("is-selected", checkbox.checked);
            });

            selectedCount.textContent = selected.length;
            selectedFilterCount.textContent = selected.length;
            visibleCount.textContent = visible.length;
            empty.hidden = visible.length > 0;
            empty.textContent = filter === "selected" && selected.length === 0 && !value
                ? "尚未选择角色"
                : "没有匹配的角色";
            selectVisible.disabled = filter !== "all" || matching.length === 0;
            clearRoles.disabled = selected.length === 0;
        }

        picker.querySelectorAll("[data-role-filter]").forEach(function (button) {
            button.addEventListener("click", function () {
                filter = button.getAttribute("data-role-filter");
                picker.querySelectorAll("[data-role-filter]").forEach(function (item) {
                    var active = item === button;
                    item.classList.toggle("active", active);
                    item.setAttribute("aria-pressed", active ? "true" : "false");
                });
                renderOptions();
            });
        });

        search.addEventListener("input", renderOptions);
        picker.querySelector("[data-role-clear-search]").addEventListener("click", function () {
            search.value = "";
            search.focus();
            renderOptions();
        });
        options.forEach(function (option) {
            option.querySelector("[data-role-checkbox]").addEventListener("change", renderOptions);
        });
        selectVisible.addEventListener("click", function () {
            var value = (search.value || "").trim().toLocaleLowerCase();
            options.forEach(function (option) {
                if (optionMatchesSearch(option, value)) {
                    option.querySelector("[data-role-checkbox]").checked = true;
                }
            });
            renderOptions();
        });
        clearRoles.addEventListener("click", function () {
            options.forEach(function (option) {
                option.querySelector("[data-role-checkbox]").checked = false;
            });
            renderOptions();
        });

        renderOptions();
        Array.prototype.forEach.call(controls, function (element) {
            element.hidden = false;
        });
    });
}());
