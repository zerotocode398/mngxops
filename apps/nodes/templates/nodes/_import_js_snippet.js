    /** 节点批量导入：选择文件并提交 */
    (function initNodeBatchImport() {
        var dropzone = document.getElementById('nodeImportDropzone');
        var fileInput = document.getElementById('nodeImportFileInput');
        var fileNameEl = document.getElementById('nodeImportFileName');
        var submitBtn = document.getElementById('nodeImportSubmitBtn');
        if (!dropzone || !fileInput || !submitBtn) return;

        var csrfToken = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
        var importUrl = '{% url "nodes:import_api" %}';
        var selectedFile = null;
        var importing = false;

        function setFile(file) {
            if (!file) return;
            var lower = (file.name || '').toLowerCase();
            if (!lower.endsWith('.xlsx')) {
                if (window.showToast) showToast('仅支持 .xlsx 格式', 'danger');
                fileInput.value = '';
                selectedFile = null;
                fileNameEl.textContent = '未选择文件';
                return;
            }
            selectedFile = file;
            fileNameEl.textContent = file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB)';
        }

        dropzone.addEventListener('click', function() { fileInput.click(); });
        dropzone.addEventListener('dragover', function(e) {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });
        dropzone.addEventListener('dragleave', function(e) {
            e.preventDefault();
            dropzone.classList.remove('dragover');
        });
        dropzone.addEventListener('drop', function(e) {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', function() {
            if (fileInput.files.length) setFile(fileInput.files[0]);
        });

        document.getElementById('nodeBatchImportModal').addEventListener('hidden.bs.modal', function() {
            selectedFile = null;
            fileInput.value = '';
            fileNameEl.textContent = '未选择文件';
            importing = false;
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="bi bi-check2"></i> 开始导入';
        });

        /** 将行错误列表渲染为表格 HTML */
        function buildImportErrorHtml(errors) {
            var html = '<p class="mb-2">校验未通过，未导入任何节点：</p>';
            html += '<div class="modal-table-scroll"><table class="table table-sm data-table modal-picker-table mb-0">';
            html += '<thead><tr><th style="width:20%">行号</th><th>错误信息</th></tr></thead><tbody>';
            (errors || []).forEach(function(err) {
                var rowLabel = err.row ? ('第 ' + err.row + ' 行') : '文件';
                html += '<tr><td>' + escapeHtml(rowLabel) + '</td><td>' + escapeHtml(err.message || '') + '</td></tr>';
            });
            html += '</tbody></table></div>';
            return html;
        }

        submitBtn.addEventListener('click', function() {
            if (importing) return;
            if (!selectedFile) {
                if (window.showToast) showToast('请先选择 Excel 文件', 'warning');
                return;
            }
            importing = true;
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 导入中...';

            var fd = new FormData();
            fd.append('file', selectedFile);

            fetch(importUrl, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': csrfToken,
                    'Accept': 'application/json'
                },
                body: fd
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                importing = false;
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-check2"></i> 开始导入';
                if (data && data.success) {
                    var modalEl = document.getElementById('nodeBatchImportModal');
                    var modal = bootstrap.Modal.getInstance(modalEl);
                    if (modal) modal.hide();
                    if (window.showToast) showToast(data.message || '导入成功', 'success');
                    setTimeout(function() { location.reload(); }, 500);
                    return;
                }
                var errs = (data && data.errors) || [];
                if (errs.length) {
                    showAlert(data.message || '导入失败', buildImportErrorHtml(errs));
                } else {
                    showAlert('导入失败', (data && data.message) || '未知错误');
                }
            })
            .catch(function() {
                importing = false;
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-check2"></i> 开始导入';
                showAlert('导入失败', '网络错误，请稍后重试');
            });
        });
    })();
