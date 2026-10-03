/* 静默轮询仪表盘统计，避免自动刷新打断页面操作。 */
(function ($, window, document) {
    "use strict";

    var $dashboard = $("[data-dashboard-refresh]");
    if (!$dashboard.length) {
        return;
    }
    var refreshMs = Number($dashboard.attr("data-refresh-ms")) || 30000;
    var toneClasses = [
        "stat-tone-primary",
        "stat-tone-success",
        "stat-tone-danger",
        "stat-tone-warning",
        "stat-tone-info",
        "stat-tone-neutral"
    ];

    function updateStats(stats) {
        $dashboard.find("[data-stat]").each(function () {
            var key = this.getAttribute("data-stat");
            if (Object.prototype.hasOwnProperty.call(stats, key)) {
                this.textContent = stats[key];
                var card = this.closest("[data-stat-card]");
                if (card) {
                    var positiveTone = card.getAttribute("data-tone-positive");
                    var baseTone = card.getAttribute("data-tone-base");
                    var tone = positiveTone && Number(stats[key]) > 0
                        ? positiveTone
                        : baseTone;
                    card.classList.remove.apply(card.classList, toneClasses);
                    card.classList.add("stat-tone-" + tone);
                }
            }
        });
    }

    function refreshStats() {
        if (document.hidden) {
            return;
        }
        $.ajax({
            url: $dashboard.attr("data-stats-url"),
            method: "GET",
            dataType: "json",
            cache: false,
            headers: { "X-Requested-With": "XMLHttpRequest" }
        }).done(updateStats);
    }

    window.setTimeout(function () {
        refreshStats();
        window.setInterval(refreshStats, refreshMs);
    }, refreshMs);
})(window.jQuery, window, document);
