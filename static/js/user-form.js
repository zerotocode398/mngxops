(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        initializeEntityPickers();
        initializePermissionToggle();
    });

    // 初始化可搜索的角色和用户组多选弹窗。
    function initializeEntityPickers() {
        var openers = document.querySelectorAll("[data-picker-open]");
        var initialized = {};
        openers.forEach(function (opener) {
            var key = opener.getAttribute("data-picker-open");
            if (!key || initialized[key]) {
                return;
            }
            initialized[key] = true;

            var modalElement = document.querySelector('[data-picker-modal="' + key + '"]');
            var sourceInputs = Array.prototype.slice.call(
                document.querySelectorAll('[data-picker-selection="' + key + '"]')
            );
            var choices = Array.prototype.slice.call(
                document.querySelectorAll('[data-picker-choice="' + key + '"]')
            );
            var rows = modalElement ? Array.prototype.slice.call(modalElement.querySelectorAll("[data-picker-row]")) : [];
            if (!modalElement || !sourceInputs.length || !choices.length) {
                return;
            }

            var maximum = Number(modalElement.getAttribute("data-picker-limit") || 0);
            var modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
            var query = modalElement.querySelector("[data-picker-query]");
            var queryWrapper = modalElement.querySelector("[data-picker-query-wrapper]");
            var queryTags = [];
            var selectedSummary = document.querySelector('[data-picker-summary="' + key + '"]');
            var resultCount = modalElement.querySelector("[data-picker-result-count]");
            var selectedCount = modalElement.querySelector("[data-picker-selected-count]");
            var limitWarning = modalElement.querySelector("[data-picker-limit-warning]");
            var emptyMatch = modalElement.querySelector("[data-picker-no-match]");
            var confirm = modalElement.querySelector('[data-picker-confirm="' + key + '"]');
            var pageSizeControl = modalElement.querySelector("[data-picker-page-size]");
            var pageCurrent = modalElement.querySelector("[data-picker-page-current]");
            var pageCount = modalElement.querySelector("[data-picker-page-count]");
            var previousPage = modalElement.querySelector("[data-picker-page-previous]");
            var nextPage = modalElement.querySelector("[data-picker-page-next]");
            var pageSize = Number(pageSizeControl.value) || 10;
            var currentPage = 1;

            function selectedValues() {
                return sourceInputs.filter(function (input) { return input.checked; })
                    .map(function (input) { return input.value; });
            }

            function updateSummary() {
                if (!selectedSummary) {
                    return;
                }
                selectedSummary.textContent = "";
                var selected = sourceInputs.filter(function (input) { return input.checked; });
                if (!selected.length) {
                    var empty = document.createElement("span");
                    empty.className = "small text-muted";
                    empty.textContent = "未选择";
                    selectedSummary.appendChild(empty);
                    return;
                }
                selected.forEach(function (input) {
                    var badge = document.createElement("span");
                    badge.className = "query-tag-badge entity-picker-selected-tag";
                    var label = document.createElement("span");
                    label.className = "query-tag-label";
                    label.textContent = input.getAttribute("data-label") || input.value;
                    var remove = document.createElement("button");
                    remove.type = "button";
                    remove.className = "query-tag-remove";
                    remove.setAttribute("aria-label", "移除 " + label.textContent);
                    remove.title = "移除此项";
                    remove.textContent = "×";
                    remove.addEventListener("click", function () {
                        input.checked = false;
                        updateSummary();
                    });
                    badge.append(label, remove);
                    selectedSummary.appendChild(badge);
                });
            }

            function updateChoiceLimit() {
                var count = choices.filter(function (choice) { return choice.checked; }).length;
                selectedCount.textContent = String(count);
                choices.forEach(function (choice) {
                    choice.disabled = maximum > 0 && count >= maximum && !choice.checked;
                });
                if (limitWarning) {
                    limitWarning.hidden = !(maximum > 0 && count >= maximum);
                }
            }

            function renderRows(resetPage) {
                var terms = queryTags.map(function (term) { return term.toLocaleLowerCase(); });
                var matchingRows = rows.filter(function (row) {
                    var text = (row.getAttribute("data-picker-search-text") || "").toLocaleLowerCase();
                    return terms.every(function (term) { return text.indexOf(term) >= 0; });
                });
                if (resetPage) currentPage = 1;
                var totalPages = Math.ceil(matchingRows.length / pageSize);
                currentPage = totalPages ? Math.min(Math.max(currentPage, 1), totalPages) : 0;
                var firstIndex = currentPage ? (currentPage - 1) * pageSize : 0;
                var visibleSet = new Set(matchingRows.slice(firstIndex, firstIndex + pageSize));
                rows.forEach(function (row) { row.hidden = !visibleSet.has(row); });
                resultCount.textContent = String(matchingRows.length);
                pageCurrent.textContent = String(currentPage);
                pageCount.textContent = String(totalPages);
                previousPage.disabled = currentPage <= 1;
                nextPage.disabled = !totalPages || currentPage >= totalPages;
                if (emptyMatch) {
                    emptyMatch.hidden = matchingRows.length > 0 || !rows.length;
                }
            }

            function addQueryTag(value) {
                var text = String(value || "").trim();
                if (!text || queryTags.some(function (item) {
                    return item.toLocaleLowerCase() === text.toLocaleLowerCase();
                })) {
                    return;
                }
                queryTags.push(text);
                var badge = document.createElement("span");
                badge.className = "query-tag-badge";
                badge.setAttribute("data-value", text);
                var label = document.createElement("span");
                label.className = "query-tag-label";
                label.textContent = text;
                var remove = document.createElement("button");
                remove.type = "button";
                remove.className = "query-tag-remove";
                remove.setAttribute("aria-label", "移除搜索条件 " + text);
                remove.title = "移除此条件";
                remove.textContent = "×";
                remove.addEventListener("click", function () {
                    queryTags = queryTags.filter(function (item) { return item !== text; });
                    badge.remove();
                    renderRows(true);
                    query.focus();
                });
                badge.append(label, remove);
                queryWrapper.insertBefore(badge, query);
            }

            function resetChoices() {
                var selected = selectedValues();
                choices.forEach(function (choice) {
                    choice.checked = selected.indexOf(choice.value) >= 0;
                    choice.disabled = false;
                });
                updateChoiceLimit();
                updateSummary();
            }

            openers.forEach(function (button) {
                if (button.getAttribute("data-picker-open") === key) {
                    button.addEventListener("click", function () {
                        resetChoices();
                        modal.show();
                    });
                }
            });

            choices.forEach(function (choice) {
                choice.addEventListener("change", updateChoiceLimit);
            });
            modalElement.querySelector("tbody").addEventListener("click", function (event) {
                if (event.target.closest("button, a, label, input")) {
                    return;
                }
                var row = event.target.closest("[data-picker-row]");
                var choice = row && row.querySelector('[data-picker-choice="' + key + '"]');
                if (choice && !choice.disabled) {
                    choice.checked = !choice.checked;
                    updateChoiceLimit();
                }
            });
            query.addEventListener("keydown", function (event) {
                if (event.key === "Enter" && !event.isComposing) {
                    event.preventDefault();
                    var previousCount = queryTags.length;
                    (query.value || "").split(/[,，]/).forEach(addQueryTag);
                    query.value = "";
                    renderRows(queryTags.length !== previousCount);
                } else if (event.key === "Backspace" && !query.value && queryTags.length) {
                    queryTags.pop();
                    queryWrapper.querySelectorAll(".query-tag-badge").item(queryTags.length).remove();
                    renderRows(true);
                }
            });
            pageSizeControl.addEventListener("change", function () {
                pageSize = Number(pageSizeControl.value) || 10;
                renderRows(true);
            });
            previousPage.addEventListener("click", function () {
                currentPage -= 1;
                renderRows(false);
            });
            nextPage.addEventListener("click", function () {
                currentPage += 1;
                renderRows(false);
            });
            queryWrapper.addEventListener("click", function (event) {
                if (event.target === queryWrapper) query.focus();
            });
            modalElement.addEventListener("hidden.bs.modal", function () {
                queryTags = [];
                queryWrapper.querySelectorAll(".query-tag-badge").forEach(function (tag) { tag.remove(); });
                query.value = "";
                renderRows(true);
            });
            confirm.addEventListener("click", function () {
                var selected = {};
                choices.forEach(function (choice) {
                    if (choice.checked) selected[choice.value] = true;
                });
                sourceInputs.forEach(function (input) {
                    input.checked = Boolean(selected[input.value]);
                });
                updateSummary();
                modal.hide();
            });

            renderRows(true);
            updateSummary();
        });
    }

    // 切换用户直授权限矩阵的全选状态。
    function initializePermissionToggle() {
        var button = document.querySelector("[data-permission-toggle]");
        if (!button) {
            return;
        }
        button.addEventListener("click", function () {
            var checks = document.querySelectorAll("input[name='permission_ids']");
            var selectAll = Array.prototype.some.call(checks, function (checkbox) {
                return !checkbox.checked;
            });
            checks.forEach(function (checkbox) { checkbox.checked = selectAll; });
            button.textContent = selectAll ? "清空选择" : "全局全选";
        });
    }
}());
