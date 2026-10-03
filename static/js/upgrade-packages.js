(function ($, window, document) {
    "use strict";

    var form = document.getElementById("packageUploadForm");
    if (!form) return;
    var fileInput = document.getElementById("packageFile");
    var nameInput = document.getElementById("packageName");
    var versionInput = document.getElementById("packageVersion");
    var overwriteInput = document.getElementById("overwriteInput");
    var button = document.getElementById("uploadButton");
    var progressWrap = document.getElementById("uploadProgressWrap");
    var progressBar = document.getElementById("uploadProgressBar");
    var progressLabel = document.getElementById("uploadProgressLabel");
    var progressPercent = document.getElementById("uploadProgressPercent");
    var fileSummary = document.getElementById("fileSummary");
    var messageBox = document.getElementById("uploadMessage");
    var busy = false;

    function allowedArchive(filename) {
        var lowered = (filename || "").toLowerCase();
        return form.dataset.kind === "source"
            ? lowered.endsWith(".tar.gz") || lowered.endsWith(".tgz")
            : lowered.endsWith(".tar.gz") || lowered.endsWith(".tgz") || lowered.endsWith(".zip");
    }

    function showMessage(message, kind) {
        messageBox.textContent = message || "上传失败";
        messageBox.className = "alert mt-3 mb-0 alert-" + (kind || "danger");
        messageBox.classList.remove("d-none");
    }

    function resetButton() {
        busy = false;
        button.disabled = false;
        button.innerHTML = '<i class="bi bi-upload me-1" aria-hidden="true"></i>上传并校验';
    }

    function doUpload(overwrite) {
        if (busy || !fileInput.files.length) return;
        busy = true;
        overwriteInput.value = overwrite ? "1" : "";
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>上传中';
        progressWrap.classList.remove("d-none");
        messageBox.classList.add("d-none");
        progressBar.style.width = "0%";
        progressLabel.textContent = "准备上传 " + fileInput.files[0].name;
        progressPercent.textContent = "0%";

        var request = new XMLHttpRequest();
        request.open("POST", form.action, true);
        request.setRequestHeader("X-Requested-With", "XMLHttpRequest");
        request.setRequestHeader("Accept", "application/json");
        request.setRequestHeader("X-CSRFToken", document.querySelector("meta[name='csrf-token']").content);
        request.upload.onprogress = function (event) {
            if (!event.lengthComputable) return;
            var percent = Math.min(99, Math.round(event.loaded / event.total * 100));
            progressBar.style.width = percent + "%";
            progressPercent.textContent = percent + "%";
            progressLabel.textContent = "正在上传归档";
        };
        request.onload = function () {
            var data;
            try { data = JSON.parse(request.responseText); } catch (error) { data = null; }
            if (request.status >= 200 && request.status < 300 && data && data.success) {
                progressBar.style.width = "100%";
                progressBar.classList.remove("progress-bar-animated");
                progressBar.classList.add("bg-success");
                progressPercent.textContent = "100%";
                progressLabel.textContent = "上传并校验完成";
                showMessage(data.message, "success");
                window.setTimeout(function () { window.location.assign(data.redirect); }, 500);
                return;
            }
            resetButton();
            if (data && (data.need_overwrite || data.md5_duplicate)) {
                window.showConfirm("确认覆盖", data.message, function () { doUpload(true); });
                return;
            }
            showMessage(data && data.message ? data.message : "上传失败，请重试", "danger");
            if (window.showToast) window.showToast(data && data.message ? data.message : "上传失败", "danger");
        };
        request.onerror = function () {
            resetButton();
            showMessage("网络错误，包未能上传", "danger");
        };
        request.onabort = function () {
            resetButton();
            showMessage("上传已中止", "warning");
        };
        request.send(new FormData(form));
    }

    fileInput.addEventListener("change", function () {
        var file = fileInput.files[0];
        fileSummary.textContent = "";
        if (!file) return;
        if (!allowedArchive(file.name)) {
            fileInput.value = "";
            if (window.showToast) window.showToast("归档格式不支持", "danger");
            return;
        }
        if (file.size > window.NGXOPS_PACKAGE_LIMIT_MB * 1024 * 1024) {
            fileInput.value = "";
            if (window.showToast) window.showToast("文件不能超过 " + window.NGXOPS_PACKAGE_LIMIT_MB + " MB", "danger");
            return;
        }
        fileSummary.textContent = file.name + " · " + (file.size / 1048576).toFixed(2) + " MB";
        if (form.dataset.kind === "source" && !versionInput.value) {
            var match = file.name.match(/nginx-([0-9]+(?:\.[0-9]+){1,3})/i);
            if (match) versionInput.value = match[1];
        }
        if (form.dataset.kind === "module" && !nameInput.value) {
            nameInput.value = file.name.replace(/\.(tar\.gz|tgz|zip)$/i, "").replace(/[-_](v?\d+(?:\.\d+)*)+$/i, "");
        }
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        doUpload(false);
    });
})(window.jQuery, window, document);
