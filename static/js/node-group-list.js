(function ($, window) {
    "use strict";

    // 初始化节点组删除确认交互。
    $(function () {
        var notice = $(".node-group-notice").attr("data-message");
        if (notice && window.showToast) window.showToast(notice, "success");

        // 通过全局确认框提交节点组删除请求。
        $(".node-group-delete").on("click", function () {
            var name = this.getAttribute("data-group-name") || "所选节点组";
            window.submitPostConfirm(
                "删除节点组",
                "确定删除节点组“" + name + "”吗？节点资产不会删除，但该组与节点的关联会解除。",
                this.getAttribute("data-delete-url")
            );
        });
    });
}(jQuery, window));
