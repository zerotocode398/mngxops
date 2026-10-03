/* 全站交互使用 jQuery 委托，支持页面片段异步更新。 */
(function ($, window, document) {
    "use strict";

    var pollSeconds = Number(document.body && document.body.dataset.taskPollInterval);
    window.NGXOPS_TASK_POLL_INTERVAL = (pollSeconds > 0 ? pollSeconds : 2) * 1000;

    var confirmModal;
    var alertModal;
    var confirmCallback = null;
    var acceptedCallback = null;
    var queuedConfirm = null;
    var confirmElement = document.getElementById("ngxopsConfirmModal");
    var alertElement = document.getElementById("ngxopsAlertModal");
    if (confirmElement && alertElement && window.bootstrap) {
        confirmModal = bootstrap.Modal.getOrCreateInstance(confirmElement);
        alertModal = bootstrap.Modal.getOrCreateInstance(alertElement);
    }
    var csrfToken = $("meta[name='csrf-token']").attr("content");
    if (csrfToken) {
        $.ajaxSetup({ headers: { "X-CSRFToken": csrfToken } });
    }

    function safelyRead(storage, key, fallback) {
        if (!storage) {
            return fallback;
        }
        try {
            return storage.getItem(key) || fallback;
        } catch (error) {
            return fallback;
        }
    }

    function safelyWrite(storage, key, value) {
        if (!storage) {
            return;
        }
        try {
            storage.setItem(key, value);
        } catch (error) {}
    }

    function getStorage(name) {
        try {
            return window[name];
        } catch (error) {
            return null;
        }
    }

    function closeSubmenus() {
        $(".sidebar .submenu.show").removeClass("show");
        $(".sidebar .nav-item-wrap").removeClass("submenu-open");
        $(".sidebar [data-submenu-toggle]")
            .attr("aria-expanded", "false")
            .removeClass("expanded");
    }

    function setSidebarCollapsed(collapsed) {
        $(document.documentElement).toggleClass("sidebar-collapsed", collapsed);
        $(document.body).removeClass("sidebar-mobile-open");
        safelyWrite(getStorage("localStorage"), "mngxops_sidebar_collapsed", collapsed ? "1" : "0");
        closeSubmenus();
        $("#sidebarToggleBtn")
            .attr("title", collapsed ? "展开菜单" : "收起菜单")
            .attr("aria-expanded", collapsed ? "false" : "true");
        if (!collapsed) {
            var $activeParent = $(".sidebar .submenu .nav-link.active").first().closest(".submenu");
            if ($activeParent.length) {
                $activeParent.addClass("show");
                $("[data-target='" + $activeParent.attr("id") + "']").attr("aria-expanded", "true");
            }
        }
    }

    function toggleSubmenu($trigger) {
        var $submenu = $("#" + $trigger.attr("data-target"));
        if (!$submenu.length) {
            return;
        }
        var opening = !$submenu.hasClass("show");
        var collapsed = $(document.documentElement).hasClass("sidebar-collapsed");
        if (collapsed) {
            closeSubmenus();
        }
        $submenu.toggleClass("show", opening);
        $trigger.attr("aria-expanded", opening ? "true" : "false");
        if (collapsed) {
            $trigger.closest(".nav-item-wrap").toggleClass("submenu-open", opening);
        } else {
            var expanded = $(".sidebar .submenu.show").map(function () {
                return this.id;
            }).get();
            safelyWrite(getStorage("sessionStorage"), "mngxops_expanded_submenus", JSON.stringify(expanded));
        }
    }

    function applyConfirmSize(size) {
        var $dialog = $("#ngxopsConfirmModal .modal-dialog");
        $dialog.removeClass("modal-sm modal-lg modal-xl");
        if (size === "sm" || !size) {
            $dialog.addClass("modal-sm");
        } else if (size === "lg" || size === "xl") {
            $dialog.addClass("modal-" + size);
        }
    }

    function resolveConfirmSize(asHtml, message, size) {
        if (size) {
            return size;
        }
        if (!asHtml) {
            return "sm";
        }
        return /<table[\s>]/i.test(message || "") ? "lg" : "md";
    }

    function displayConfirm(options) {
        applyConfirmSize(options.size);
        $("#ngxopsConfirmTitle").text(options.title || "确认操作");
        if (options.asHtml) {
            $("#ngxopsConfirmBody").html(options.message || "");
        } else {
            $("#ngxopsConfirmBody").text(options.message || "");
        }
        $("#ngxopsConfirmBtn").text(options.confirmLabel || "确认").prop("disabled", false);
        confirmCallback = options.onConfirm;
        confirmModal.show();
    }

    function showToast(message, type, duration) {
        var icons = {
            success: "bi-check-circle-fill",
            danger: "bi-x-circle-fill",
            warning: "bi-exclamation-triangle-fill",
            info: "bi-info-circle-fill"
        };
        var kind = icons[type] ? type : "info";
        var lifetime = Number(duration);
        if (!isFinite(lifetime) || lifetime <= 0) {
            lifetime = 3000;
        }
        var $toast = $("<div>", { "class": "toast-item toast-" + kind, role: "status" });
        $("<i>", { "class": "bi " + icons[kind], "aria-hidden": "true" }).appendTo($toast);
        $("<span>").text(message == null ? "" : String(message)).appendTo($toast);
        var $close = $("<button>", {
            "class": "toast-close",
            type: "button",
            title: "关闭提示",
            "aria-label": "关闭提示"
        });
        $("<i>", { "class": "bi bi-x-lg", "aria-hidden": "true" }).appendTo($close);
        $close.appendTo($toast);
        $("<div>", { "class": "toast-progress", "aria-hidden": "true" })
            .css("animation-duration", lifetime + "ms")
            .appendTo($toast);
        $("#toastContainer").append($toast);
        window.setTimeout(function () {
            if (!$toast.parent().length) {
                return;
            }
            $toast.addClass("removing");
            window.setTimeout(function () { $toast.remove(); }, 220);
        }, lifetime);
    }

    function showAlert(title, message, asHtml) {
        $("#ngxopsAlertTitle").text(title || "提示");
        if (asHtml) {
            $("#ngxopsAlertBody").html(message || "");
        } else {
            $("#ngxopsAlertBody").text(message || "");
        }
        alertModal.show();
    }

    function initializeNavigation() {
        var $active = $(".sidebar .submenu .nav-link.active").first();
        if (!$active.length) {
            $active = $(".sidebar .nav-link.active").first();
        }
        if (!$(document.documentElement).hasClass("sidebar-collapsed")) {
            var activeParent = $active.closest(".submenu");
            if (activeParent.length) {
                activeParent.addClass("show");
                $("[data-target='" + activeParent.attr("id") + "']").attr("aria-expanded", "true");
            }
            var saved = safelyRead(getStorage("sessionStorage"), "mngxops_expanded_submenus", "[]");
            try {
                JSON.parse(saved).forEach(function (id) {
                    var $submenu = $("#" + id);
                    if ($submenu.length) {
                        $submenu.addClass("show");
                        $("[data-target='" + id + "']").attr("aria-expanded", "true");
                    }
                });
            } catch (error) {}
        } else {
            closeSubmenus();
        }
        if ($active.length && window.matchMedia("(min-width: 768px)").matches) {
            $active[0].scrollIntoView({ block: "nearest" });
        }
        var collapsed = $(document.documentElement).hasClass("sidebar-collapsed");
        $("#sidebarToggleBtn")
            .attr("title", collapsed ? "展开菜单" : "收起菜单")
            .attr("aria-expanded", collapsed ? "false" : "true");
    }

    $(function () {
        initializeNavigation();

        $("#sidebarToggleBtn").on("click", function () {
            if (window.matchMedia("(max-width: 767.98px)").matches) {
                $(document.body).toggleClass("sidebar-mobile-open");
                return;
            }
            setSidebarCollapsed(!$(document.documentElement).hasClass("sidebar-collapsed"));
        });

        $(document).on("click", "[data-submenu-toggle]", function () {
            toggleSubmenu($(this));
        });

        $(document).on("click", function (event) {
            if ($(document.documentElement).hasClass("sidebar-collapsed") &&
                !$(event.target).closest(".sidebar").length) {
                closeSubmenus();
            }
            if ($(document.body).hasClass("sidebar-mobile-open") &&
                !$(event.target).closest(".sidebar, #sidebarToggleBtn").length) {
                $(document.body).removeClass("sidebar-mobile-open");
            }
        });

        $(document).on("click", ".toast-close", function () {
            $(this).closest(".toast-item").remove();
        });

        $(document).on("change", "select[name='per_page']", function () {
            this.form.submit();
        });

        $(document).on("submit", "form[data-confirm]", function (event) {
            var form = this;
            if (form.dataset.confirmed === "1") {
                delete form.dataset.confirmed;
                return;
            }
            event.preventDefault();
            window.showConfirm(form.dataset.confirmTitle || "确认操作", form.dataset.confirm, function () {
                form.dataset.confirmed = "1";
                form.requestSubmit();
            });
        });

        var alertPayload = $("#permission-alert-payload");
        if (alertPayload.length) {
            showToast(alertPayload.attr("data-message"), "danger");
        }

        $(".alert-auto-dismiss").each(function () {
            var alert = this;
            window.setTimeout(function () {
                $(alert).css("opacity", "0");
                window.setTimeout(function () { $(alert).remove(); }, 350);
            }, 3000);
        });

    });

    $(document).on("hidden.bs.modal", "#ngxopsConfirmModal", function () {
        $("#ngxopsConfirmBtn").prop("disabled", false).text("确认");
        var callback = acceptedCallback;
        acceptedCallback = null;
        if (!callback) {
            confirmCallback = null;
        }
        if (callback) {
            callback();
        }
        if (queuedConfirm) {
            var next = queuedConfirm;
            queuedConfirm = null;
            displayConfirm(next);
        }
    });

    $(document).on("shown.bs.modal", "#ngxopsConfirmModal", function () {
        if (queuedConfirm && !acceptedCallback) {
            confirmModal.hide();
        }
    });

    $(document).on("click", "#ngxopsConfirmBtn", function () {
        acceptedCallback = confirmCallback;
        confirmCallback = null;
        confirmModal.hide();
    });

    window.showToast = showToast;
    window.showAlert = showAlert;
    window.showConfirm = function (title, message, onConfirm, asHtml, size, confirmLabel) {
        var options = {
            title: title,
            message: message,
            onConfirm: onConfirm,
            asHtml: !!asHtml,
            size: resolveConfirmSize(!!asHtml, message, size),
            confirmLabel: confirmLabel
        };
        var $element = $("#ngxopsConfirmModal");
        if ($element.hasClass("show") || $element.hasClass("showing") || $element.hasClass("hiding") || acceptedCallback) {
            queuedConfirm = options;
            if ($element.hasClass("show") && !acceptedCallback) {
                confirmModal.hide();
            }
            return;
        }
        displayConfirm(options);
    };
    window.submitPostConfirm = function (title, message, actionUrl) {
        window.showConfirm(title, message, function () {
            var $form = $("<form>", { method: "post", action: actionUrl });
            var token = $("meta[name='csrf-token']").attr("content") || "";
            $("<input>", { type: "hidden", name: "csrf_token", value: token }).appendTo($form);
            $form.appendTo(document.body).trigger("submit");
        });
    };
    window.bindModalTableRowToggle = function (tbodyOrSelector, checkboxSelector) {
        var $tbody = typeof tbodyOrSelector === "string" ? $(tbodyOrSelector) : $(tbodyOrSelector);
        $tbody.off("click.ngxopsRowToggle", "tr").on("click.ngxopsRowToggle", "tr", function (event) {
            if ($(event.target).closest("button, a, label, input, select").length) {
                return;
            }
            var $checkbox = $(this).find(checkboxSelector).first();
            if ($checkbox.length && !$checkbox.prop("disabled")) {
                $checkbox.prop("checked", !$checkbox.prop("checked")).trigger("change");
            }
        });
    };
})(window.jQuery, window, document);
