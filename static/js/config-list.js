(function ($, window, document) {
    "use strict";

    $(function () {
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
    });
})(window.jQuery, window, document);
