(function ($, window, document) {
    "use strict";

    var form = document.getElementById("packageUploadForm");
    if (!form) return;
    var dropZone = document.getElementById("packageDropzone");
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
    var packageLimitMb = Number(form.dataset.packageLimitMb) || 20;
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

    function inspectFile(file) {
        fileSummary.classList.add("d-none");
        fileSummary.textContent = "";
        if (!file) return false;
        if (!allowedArchive(file.name)) {
            fileInput.value = "";
            if (window.showToast) window.showToast("归档格式不支持", "danger");
            return false;
        }
        if (file.size > packageLimitMb * 1024 * 1024) {
            fileInput.value = "";
            if (window.showToast) window.showToast("文件不能超过 " + packageLimitMb + " MB", "danger");
            return false;
        }
        fileSummary.textContent = file.name + " · " + (file.size / 1048576).toFixed(2) + " MB";
        fileSummary.classList.remove("d-none");
        if (form.dataset.kind === "source" && !versionInput.value) {
            var match = file.name.match(/nginx-([0-9]+(?:\.[0-9]+){1,3})/i);
            if (match) versionInput.value = match[1];
        }
        if (form.dataset.kind === "module" && !nameInput.value) {
            nameInput.value = file.name.replace(/\.(tar\.gz|tgz|zip)$/i, "").replace(/[-_](v?\d+(?:\.\d+)*)+$/i, "");
        }
        return true;
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
            if (window.showToast) window.showToast("网络错误，包未能上传", "danger");
        };
        request.onabort = function () {
            resetButton();
            showMessage("上传已中止", "warning");
            if (window.showToast) window.showToast("上传已中止", "warning");
        };
        request.send(new FormData(form));
    }

    dropZone.addEventListener("click", function (event) {
        if (event.target !== fileInput) fileInput.click();
    });
    dropZone.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        fileInput.click();
    });
    dropZone.addEventListener("dragover", function (event) {
        event.preventDefault();
        dropZone.classList.add("dragover");
    });
    dropZone.addEventListener("dragleave", function (event) {
        event.preventDefault();
        if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove("dragover");
    });
    dropZone.addEventListener("drop", function (event) {
        event.preventDefault();
        dropZone.classList.remove("dragover");
        if (!event.dataTransfer.files.length) return;
        fileInput.files = event.dataTransfer.files;
        inspectFile(fileInput.files[0]);
    });
    fileInput.addEventListener("change", function () {
        inspectFile(fileInput.files[0]);
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!fileInput.files.length) {
            if (window.showToast) window.showToast("请选择归档文件", "warning");
            return;
        }
        doUpload(false);
    });
})(window.jQuery, window, document);
