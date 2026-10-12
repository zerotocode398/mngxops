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
    var fileSummaryText = document.getElementById("fileSummaryText");
    var messageBox = document.getElementById("uploadMessage");
    var packageLimitMb = Number(form.dataset.packageLimitMb) || 20;
    var busy = false;
    var autoPackageName = "";
    var autoPackageVersion = "";
    var packageNameManuallyEdited = false;
    var packageVersionManuallyEdited = false;

    nameInput.addEventListener("input", function () {
        packageNameManuallyEdited = true;
    });
    versionInput.addEventListener("input", function () {
        packageVersionManuallyEdited = true;
    });

    // 从源码包归档文件名中去掉扩展名和版本号。
    function inferSourceName(filename) {
        var stem = filename.replace(/\.(tar\.gz|tgz)$/i, "");
        var match = stem.match(/^(.*?)[-_]v?\d+(?:\.\d+){1,3}(?:[-+].*)?$/i);
        return (match ? match[1] : stem).trim() || "nginx";
    }

    // 从标准 Nginx 源码归档文件名中提取版本号。
    function inferSourceVersion(filename) {
        var match = filename.match(/nginx-([0-9]+(?:\.[0-9]+){1,3})/i);
        return match ? match[1] : "";
    }

    function inferModuleName(filename) {
        var stem = filename.replace(/\.(tar\.gz|tgz|zip)$/i, "");
        var match = stem.match(/[-_](v?\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.-]+)?)$/i);
        return (match ? stem.slice(0, match.index) : stem).trim() || stem;
    }

    function inferModuleVersion(filename) {
        var stem = filename.replace(/\.(tar\.gz|tgz|zip)$/i, "");
        var match = stem.match(/[-_](v?\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.-]+)?)$/i);
        return match ? match[1] : "";
    }

    function updateInferredField(input, inferred, previousValue, manuallyEdited) {
        var current = input.value.trim();
        if (!manuallyEdited && (!current || current === previousValue)) {
            input.value = inferred;
            return inferred;
        }
        return previousValue;
    }

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
        fileSummaryText.textContent = "";
        if (!file) return false;
        if (!allowedArchive(file.name)) {
            fileInput.value = "";
            showMessage(form.dataset.kind === "source"
                ? "源码包仅支持 .tar.gz / .tgz 格式"
                : "模块包仅支持 .tar.gz / .tgz / .zip 格式", "danger");
            return false;
        }
        if (file.size > packageLimitMb * 1024 * 1024) {
            fileInput.value = "";
            showMessage("文件不能超过 " + packageLimitMb + " MB", "danger");
            return false;
        }
        fileSummaryText.textContent = file.name + " (" + (file.size / 1048576).toFixed(2) + " MB)";
        fileSummary.classList.remove("d-none");
        if (form.dataset.kind === "source") {
            autoPackageName = updateInferredField(
                nameInput, inferSourceName(file.name), autoPackageName, packageNameManuallyEdited
            );
            autoPackageVersion = updateInferredField(
                versionInput, inferSourceVersion(file.name), autoPackageVersion, packageVersionManuallyEdited
            );
        } else {
            autoPackageName = updateInferredField(
                nameInput, inferModuleName(file.name), autoPackageName, packageNameManuallyEdited
            );
            autoPackageVersion = updateInferredField(
                versionInput, inferModuleVersion(file.name), autoPackageVersion, packageVersionManuallyEdited
            );
        }
        return true;
    }

    function validateFields(file) {
        if (!file) return "请选择归档文件";
        if (!allowedArchive(file.name)) return "归档格式不受支持";
        if (file.size > packageLimitMb * 1024 * 1024) {
            return "文件不能超过 " + packageLimitMb + " MB";
        }
        var name = nameInput.value.trim();
        var version = versionInput.value.trim();
        if (!name) return form.dataset.kind === "source" ? "请填写源码包名称" : "请填写模块名称";
        if (name.length > 100) return "名称不能超过 100 个字符";
        if (form.dataset.kind === "source" && !version) {
            return "请输入版本号或选择具有 nginx-版本号 命名的文件";
        }
        if (version.length > 50) return "版本号不能超过 50 个字符";
        if (form.dataset.kind === "module" && !/^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$/.test(name)) {
            return "模块名只允许字母、数字、点、下划线、加号和连字符";
        }
        return "";
    }

    function readHeader(file) {
        var slice = file.slice(0, 4);
        if (slice.arrayBuffer) {
            return slice.arrayBuffer().then(function (buffer) {
                return new Uint8Array(buffer);
            });
        }
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () { resolve(new Uint8Array(reader.result)); };
            reader.onerror = function () { reject(new Error("无法读取归档文件")); };
            reader.readAsArrayBuffer(slice);
        });
    }

    function validateArchiveSignature(file) {
        return readHeader(file).then(function (bytes) {
            var lowered = file.name.toLowerCase();
            var gzip = bytes.length >= 2 && bytes[0] === 31 && bytes[1] === 139;
            var zip = bytes.length >= 4 && bytes[0] === 80 && bytes[1] === 75 &&
                ((bytes[2] === 3 && bytes[3] === 4) ||
                    (bytes[2] === 5 && bytes[3] === 6) ||
                    (bytes[2] === 7 && bytes[3] === 8));
            if ((lowered.endsWith(".zip") && !zip) ||
                ((lowered.endsWith(".tar.gz") || lowered.endsWith(".tgz")) && !gzip)) {
                throw new Error("文件内容与所选归档格式不匹配");
            }
        });
    }

    function checkVersionConflict() {
        var kind = form.dataset.kind;
        var data = { name: nameInput.value.trim(), version: versionInput.value.trim() };
        return $.ajax({
            method: "GET",
            url: kind === "source" ? "/api/upgrade/packages/check" : "/api/upgrade/modules/check",
            data: data,
            dataType: "json"
        });
    }

    function beginTransfer(overwrite) {
        if (busy && !overwrite) return;
        busy = true;
        overwriteInput.value = overwrite ? "1" : "";
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>上传中';
        progressWrap.classList.remove("d-none");
        messageBox.classList.add("d-none");
        progressBar.style.width = "0%";
        progressBar.classList.remove("bg-success", "bg-danger", "progress-bar-animated");
        progressBar.classList.add("progress-bar-animated");
        progressLabel.textContent = "正在上传归档 " + fileInput.files[0].name;
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
                progressLabel.textContent = "归档校验完成";
                window.setTimeout(function () { window.location.assign(data.redirect); }, 500);
                return;
            }
            resetButton();
            if (data && (data.need_overwrite || data.md5_duplicate)) {
                progressBar.style.width = "100%";
                progressBar.classList.remove("bg-success", "bg-danger", "progress-bar-animated");
                progressBar.classList.add("bg-info");
                progressPercent.textContent = "100%";
                progressLabel.textContent = "归档已接收，等待覆盖确认";
                window.showConfirm("确认覆盖", data.message, function () { beginTransfer(true); });
                return;
            }
            progressWrap.classList.add("d-none");
            showMessage(data && data.message ? data.message : "上传失败，请重试", "danger");
            if (window.showToast) window.showToast(data && data.message ? data.message : "上传失败", "danger");
        };
        request.onerror = function () {
            resetButton();
            progressWrap.classList.add("d-none");
            showMessage("网络错误，归档未能上传", "danger");
        };
        request.onabort = function () {
            resetButton();
            progressWrap.classList.add("d-none");
            showMessage("上传已中止", "warning");
        };
        request.send(new FormData(form));
    }

    async function preflightAndUpload() {
        if (busy) return;
        var file = fileInput.files[0];
        if (!file) {
            messageBox.classList.add("d-none");
            if (window.showToast) window.showToast("请选择归档文件", "warning");
            return;
        }
        var validationError = validateFields(file);
        if (validationError) {
            if (form.dataset.kind === "source" && validationError === "请填写源码包名称") {
                messageBox.classList.add("d-none");
                if (window.showToast) window.showToast(validationError, "warning");
                return;
            }
            showMessage(validationError, "danger");
            if (window.showToast) window.showToast(validationError, "warning");
            return;
        }

        busy = true;
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>正在检查';
        messageBox.classList.add("d-none");
        try {
            await validateArchiveSignature(file);
            var conflict = await checkVersionConflict();
            if (conflict.exists) {
                resetButton();
                var packageLabel = form.dataset.kind === "source" ? "源码包" : "模块包";
                var packageIdentity = nameInput.value.trim();
                if (versionInput.value.trim()) {
                    packageIdentity += " " + versionInput.value.trim();
                }
                window.showConfirm(
                    "确认覆盖",
                    packageLabel + "“" + packageIdentity + "”已存在，是否覆盖？",
                    function () {
                        beginTransfer(true);
                    }
                );
                return;
            }
            busy = false;
            beginTransfer(false);
        } catch (error) {
            resetButton();
            var message = error && error.message ? error.message :
                (error && error.responseJSON && error.responseJSON.message) || "上传前校验失败，请检查后重试";
            showMessage(message, "danger");
            if (window.showToast) window.showToast(message, "danger");
        }
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
    fileInput.addEventListener("change", function () { inspectFile(fileInput.files[0]); });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        preflightAndUpload();
    });
})(window.jQuery, window, document);
