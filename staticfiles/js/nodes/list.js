/* ========== 节点列表页 JS ========== */
/* 依赖 base.html 中的全局函数：showConfirm, showAlert, showAsyncProgressOverlay,
   closeAsyncProgressOverlay, startAsyncProgressPolling, bindModalTableRowToggle, initTagSearch */

var nodeDetailModal;
var currentDetailNodeId = null;
var testTimer = null;
var testSeconds = 0;
var MAX_SELECT;

document.addEventListener('DOMContentLoaded', function () {
    MAX_SELECT = NODE_LIST_CONFIG.batchMaxCount;

    nodeDetailModal = new bootstrap.Modal(document.getElementById('nodeDetailModal'));

    initTagSearch('#nodeSearchTagWrapper', '#nodeSearchHidden', '#searchForm');

    document.querySelectorAll('.nginx-version-cell').forEach(function (cell) {
        var v = cell.getAttribute('data-nginx-version');
        if (v) cell.textContent = formatNginxVersion(v);
    });

    document.querySelectorAll('.group-filter-badge').forEach(function (badge) {
        badge.addEventListener('click', function (e) {
            e.stopPropagation();
            var gs = document.getElementById('groupSearch');
            if (gs) {
                gs.value = this.getAttribute('data-group-name');
                document.getElementById('searchForm').submit();
            }
        });
    });

    document.querySelectorAll('.node-detail-link').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            var nodeId = this.getAttribute('data-node-id');
            openNodeDetail(nodeId);
        });
    });

    initCheckboxLogic();

    var nodeExportBtn = document.getElementById('nodeExportBtn');
    if (nodeExportBtn) {
        nodeExportBtn.addEventListener('click', function () {
            var base = nodeExportBtn.getAttribute('data-export-base') || '';
            var filterQuery = nodeExportBtn.getAttribute('data-export-query') || '';
            var selectedIds = window.getSelectedNodeIds ? window.getSelectedNodeIds() : [];
            if (selectedIds && selectedIds.length) {
                window.location.href = base + '?ids=' + selectedIds.join(',');
                return;
            }
            showConfirm(
                '确认全量导出',
                '将导出全量节点，是否继续？',
                function () {
                    var url = base;
                    if (filterQuery) {
                        url += (url.indexOf('?') >= 0 ? '&' : '?') + filterQuery;
                    }
                    window.location.href = url;
                }
            );
        });
    }

    document.querySelectorAll('.nginx-version-cell').forEach(function (cell) {
        var originalVersion = cell.getAttribute('data-nginx-version');
        if (originalVersion) {
            cell.textContent = formatNginxVersion(originalVersion);
        }
    });
});

/* ========== Nginx版本格式化 ========== */
function formatNginxVersion(version) {
    if (!version) return "-";
    var v = version.toString();
    if (v.includes("nginx version:")) {
        var parts = v.split("nginx version:");
        v = parts[parts.length - 1].trim();
    }
    if (v.startsWith("nginx/")) {
        v = v.replace("nginx/", "");
    }
    return v;
}

/* ========== 通用弹窗工具 ========== */
function showCustomModal(title, content, needConfirm, confirmCallback, iconClass, confirmIconClass, confirmBtnClass) {
    if (needConfirm && typeof confirmCallback === 'function') {
        showConfirm(title || '确认', content || '', confirmCallback, true);
        return;
    }
    showAlert(title || '提示', content || '');
}

function showLoading(title) { }

function updateLoadingContent(html) { }

function hideLoading() { }

function stopTestTimer() {
    if (testTimer) { clearInterval(testTimer); testTimer = null; }
}

/* ========== 删除确认弹窗 ========== */
function openDeleteModal(el) {
    var deleteUrl = el.getAttribute('data-delete-url') || '#';
    var hostname = el.getAttribute('data-node-hostname') || '';
    var ip = el.getAttribute('data-node-ip') || '';
    var msg = '确定要删除节点 ' + hostname + ' (' + ip + ') 吗？此操作不可恢复！';
    showConfirm('确认删除', msg, function () {
        var form = document.getElementById('deleteNodeForm');
        form.setAttribute('action', deleteUrl);
        form.submit();
    });
}

/* ========== 复选框批量选择 ========== */
function initCheckboxLogic() {
    var selectAll = document.getElementById('selectAllNodes');
    var batchTestBtn = document.getElementById('batchTestBtn');
    var batchLockBtn = document.getElementById('batchLockBtn');
    var batchUnlockBtn = document.getElementById('batchUnlockBtn');
    var batchDeleteBtn = document.getElementById('batchDeleteBtn');
    var batchSelectionHint = document.getElementById('batchSelectionHint');

    function getNodeCheckboxes() {
        return document.querySelectorAll('.node-checkbox');
    }

    function getSelectedNodeIds() {
        var ids = [];
        document.querySelectorAll('.node-checkbox:checked').forEach(function (cb) {
            ids.push(parseInt(cb.getAttribute('data-node-id')));
        });
        return ids;
    }

    function syncSelectAllState() {
        if (!selectAll) return;
        var checkboxes = getNodeCheckboxes();
        var checked = document.querySelectorAll('.node-checkbox:checked').length;
        var maxSelectable = Math.min(MAX_SELECT, checkboxes.length);
        if (checked === 0) {
            selectAll.checked = false;
            selectAll.indeterminate = false;
        } else if (checked >= maxSelectable || checked === checkboxes.length) {
            selectAll.checked = true;
            selectAll.indeterminate = false;
        } else {
            selectAll.checked = false;
            selectAll.indeterminate = true;
        }
    }

    function updateBatchUI() {
        var checked = document.querySelectorAll('.node-checkbox:checked');
        var count = checked.length;
        if (batchTestBtn) batchTestBtn.disabled = count === 0;
        if (batchLockBtn) batchLockBtn.disabled = count === 0;
        if (batchUnlockBtn) batchUnlockBtn.disabled = count === 0;
        if (batchDeleteBtn) batchDeleteBtn.disabled = count === 0;
        if (batchSelectionHint) {
            batchSelectionHint.textContent = '已选择 ' + count + '/' + MAX_SELECT;
        }
        syncSelectAllState();
    }

    if (selectAll) {
        selectAll.addEventListener('click', function () {
            var checkboxes = getNodeCheckboxes();
            var hasSelected = document.querySelectorAll('.node-checkbox:checked').length > 0;
            if (hasSelected) {
                checkboxes.forEach(function (cb) { cb.checked = false; });
            } else {
                var selected = 0;
                checkboxes.forEach(function (cb) {
                    if (selected < MAX_SELECT) { cb.checked = true; selected++; }
                    else { cb.checked = false; }
                });
            }
            updateBatchUI();
        });
    }

    document.querySelectorAll('.node-checkbox').forEach(function (cb) {
        cb.addEventListener('change', function () {
            var checked = document.querySelectorAll('.node-checkbox:checked');
            if (checked.length > MAX_SELECT) {
                cb.checked = false;
                showCustomModal('提示', '<p class="text-warning mb-0">最多只能勾选 ' + MAX_SELECT + ' 个节点</p>');
            }
            updateBatchUI();
        });
    });

    document.querySelectorAll('tbody tr').forEach(function (row) {
        row.addEventListener('click', function (e) {
            if (e.target.closest('a,button,input,label,.btn-group,.node-detail-link')) return;
            var cb = row.querySelector('.node-checkbox');
            if (!cb) return;
            cb.checked = !cb.checked;
            cb.dispatchEvent(new Event('change'));
        });
    });

    updateBatchUI();

    window.getSelectedNodeIds = getSelectedNodeIds;
}

/* ========== 单节点连接测试 ========== */
function testConnection(el) {
    var nodeId = parseInt(el.getAttribute('data-node-id'));
    var selectedNodeIds = window.getSelectedNodeIds ? window.getSelectedNodeIds() : [];
    if (selectedNodeIds.length > 1) {
        batchTestConnection(selectedNodeIds);
        return;
    }
    doSingleTest({ node_id: nodeId });
}

function testConnectionFromDetail() {
    if (!currentDetailNodeId) return;
    doSingleTest({ node_id: parseInt(currentDetailNodeId) });
}

function doSingleTest(data) {
    showAsyncProgressOverlay('SSH连接测试');
    fetch(NODE_LIST_CONFIG.urls.test, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
        },
        body: JSON.stringify(data)
    })
    .then(function (resp) { return resp.json(); })
    .then(function (result) {
        if (!result.success) {
            closeAsyncProgressOverlay();
            showCustomModal('测试失败', '<p class="text-danger">' + (result.message || '未返回task_id') + '</p>', true, null, 'bi-exclamation-triangle', 'btn-danger');
            return;
        }
        if (result.async && result.task_center_id) {
            startAsyncProgressPolling(result.task_center_id);
        }
    })
    .catch(function () {
        closeAsyncProgressOverlay();
        showCustomModal('错误', '<p class="text-danger">网络错误或服务器异常</p>');
    });
}

/* ========== 批量测试 ========== */
function batchTestConnection(nodeIdsOverride) {
    var nodeIds = Array.isArray(nodeIdsOverride) ? nodeIdsOverride : (window.getSelectedNodeIds ? window.getSelectedNodeIds() : []);
    if (nodeIds.length === 0) {
        showCustomModal('提示', '<p class="text-warning">请先选择要测试的节点</p>');
        return;
    }
    showCustomModal('批量测试连接',
        '<p>确定要对选中的 <strong>' + nodeIds.length + '</strong> 个节点进行SSH连接测试吗？</p><p class="text-muted">测试结果将更新节点状态。</p>',
        true, function () {
            showAsyncProgressOverlay('批量测试连接');
            fetch(NODE_LIST_CONFIG.urls.batchTest, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
                },
                body: JSON.stringify({ node_ids: nodeIds })
            })
            .then(function (resp) { return resp.json(); })
            .then(function (result) {
                if (!result.success) {
                    closeAsyncProgressOverlay();
                    showCustomModal('测试失败', '<p class="text-danger">' + (result.message || '创建任务失败') + '</p>', true, null, 'bi-exclamation-triangle', 'btn-danger');
                    return;
                }
                if (result.async && result.task_center_id) {
                    startAsyncProgressPolling(result.task_center_id);
                }
            })
            .catch(function () {
                closeAsyncProgressOverlay();
                showCustomModal('错误', '<p class="text-danger">网络错误或服务器异常</p>');
            });
        }, 'bi-lightning-charge', 'bi-lightning-charge', 'btn-success');
}

/* ========== 锁定/解锁 ========== */
function toggleLock(el) {
    var nodeId = parseInt(el.getAttribute('data-node-id'));
    var action = el.getAttribute('data-action');
    var isLock = action === 'lock';
    doLockAction(nodeId, action, isLock);
}

function toggleLockFromDetail() {
    if (!currentDetailNodeId) return;
    var isLocked = document.getElementById('detailLocked').textContent.indexOf('是') >= 0;
    var action = isLocked ? 'unlock' : 'lock';
    doLockAction(parseInt(currentDetailNodeId), action, !isLocked);
}

function doLockAction(nodeId, action, isLock) {
    var title = isLock ? '锁定节点' : '解锁节点';
    var confirmMsg = isLock
        ? '确定要锁定该节点吗？锁定后节点状态将变为"离线"，且不可再进行操作。'
        : '确定要解锁该节点吗？解锁后将自动测试连接并更新状态。';
    var iconClass = isLock ? 'bi-lock' : 'bi-unlock';
    var confirmBtnClass = isLock ? 'btn-danger' : 'btn-success';

    showCustomModal(title, '<p>' + confirmMsg + '</p>', true, function () {
        if (isLock) {
            showLoading(title + '中...');
            var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            fetch(NODE_LIST_CONFIG.urls.lock, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ node_id: nodeId, action: action })
            })
            .then(function (resp) { return resp.json(); })
            .then(function (result) {
                hideLoading();
                if (result.success) {
                    showCustomModal('操作完成', '<p class="text-success">' + result.message + '</p>', true, function () { location.reload(); }, 'bi-check-circle text-success');
                } else {
                    showCustomModal('操作失败', '<p class="text-danger">' + result.message + '</p>', true, null, 'bi-exclamation-triangle', 'btn-danger');
                }
            })
            .catch(function () { hideLoading(); showCustomModal('错误', '<p class="text-danger">网络错误或服务器异常</p>'); });
        } else {
            showAsyncProgressOverlay('解锁并测试连接');
            var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            fetch(NODE_LIST_CONFIG.urls.lock, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ node_id: nodeId, action: action })
            })
            .then(function (resp) { return resp.json(); })
            .then(function (result) {
                if (!result.success) {
                    closeAsyncProgressOverlay();
                    showCustomModal('操作失败', '<p class="text-danger">' + (result.message || '操作失败') + '</p>', true, null, 'bi-exclamation-triangle', 'btn-danger');
                    return;
                }
                if (result.async && result.task_center_id) {
                    startAsyncProgressPolling(result.task_center_id);
                }
            })
            .catch(function () { closeAsyncProgressOverlay(); showCustomModal('错误', '<p class="text-danger">网络错误或服务器异常</p>'); });
        }
    });
}

function batchLockNodes() {
    var nodeIds = window.getSelectedNodeIds ? window.getSelectedNodeIds() : [];
    if (nodeIds.length === 0) { showCustomModal('提示', '<p class="text-warning">请先选择要锁定的节点</p>'); return; }
    showCustomModal('批量锁定节点',
        '<p>确定要锁定选中的 <strong>' + nodeIds.length + '</strong> 个节点吗？</p><p class="text-muted">锁定后节点状态将变为"离线"，且不可再进行操作。</p>',
        true, function () {
            showLoading('批量锁定中...');
            var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            fetch(NODE_LIST_CONFIG.urls.lock, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ node_ids: nodeIds, action: 'lock' })
            })
            .then(function (resp) { return resp.json(); })
            .then(function (result) {
                hideLoading();
                if (result.success) {
                    var hostnamesHtml = result.hostnames.map(function (h) { return '<span class="badge bg-secondary me-1">' + h + '</span>'; }).join('');
                    showCustomModal('批量锁定完成', '<div class="mb-2">' + hostnamesHtml + '</div><p class="text-success">' + result.message + '</p>', true, function () { location.reload(); }, 'bi-check-circle text-success');
                } else {
                    showCustomModal('批量锁定失败', '<p class="text-danger">' + result.message + '</p>', true, null, 'bi-exclamation-triangle', 'btn-danger');
                }
            })
            .catch(function () { hideLoading(); showCustomModal('错误', '<p class="text-danger">网络错误或服务器异常</p>'); });
        }, 'bi-lock', 'bi-lock', 'btn-danger');
}

function batchUnlockNodes() {
    var nodeIds = window.getSelectedNodeIds ? window.getSelectedNodeIds() : [];
    if (nodeIds.length === 0) { showCustomModal('提示', '<p class="text-warning">请先选择要解锁的节点</p>'); return; }
    showCustomModal('批量解锁节点',
        '<p>确定要解锁选中的 <strong>' + nodeIds.length + '</strong> 个节点吗？</p><p class="text-muted">解锁后将自动测试连接并更新状态。</p>',
        true, function () {
            showAsyncProgressOverlay('批量解锁并测试连接');
            var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            fetch(NODE_LIST_CONFIG.urls.lock, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ node_ids: nodeIds, action: 'unlock' })
            })
            .then(function (resp) { return resp.json(); })
            .then(function (result) {
                if (!result.success) {
                    closeAsyncProgressOverlay();
                    showCustomModal('批量解锁失败', '<p class="text-danger">' + (result.message || '操作失败') + '</p>', true, null, 'bi-exclamation-triangle', 'btn-danger');
                    return;
                }
                if (result.async && result.task_center_id) {
                    startAsyncProgressPolling(result.task_center_id);
                }
            })
            .catch(function () { closeAsyncProgressOverlay(); showCustomModal('错误', '<p class="text-danger">网络错误或服务器异常</p>'); });
        }, 'bi-unlock', 'bi-unlock', 'btn-success');
}

function batchDeleteNodes() {
    var nodeIds = window.getSelectedNodeIds ? window.getSelectedNodeIds() : [];
    if (nodeIds.length === 0) {
        showAlert('提示', '请先选择要删除的节点');
        return;
    }
    var rows = [];
    document.querySelectorAll('.node-checkbox:checked').forEach(function (cb) {
        var hostname = cb.getAttribute('data-node-hostname') || ('#' + cb.getAttribute('data-node-id'));
        var ip = cb.getAttribute('data-node-ip') || '';
        rows.push('<tr><td>' + escapeHtml(hostname) + '</td><td><code>' + escapeHtml(ip) + '</code></td></tr>');
    });
    var html = '<p class="mb-2">确定从运维清单移除以下 <strong>' + nodeIds.length + '</strong> 个节点吗？</p>';
    html += '<div class="table-responsive modal-table-scroll" style="max-height:40vh">';
    html += '<table class="table table-sm data-table mb-0"><thead><tr><th>主机名</th><th>IP</th></tr></thead><tbody>';
    html += rows.join('') + '</tbody></table></div>';
    html += '<p class="small text-muted mt-2 mb-0">发布/升级历史会保留；使用相同 IP 再次添加可恢复并关联历史。</p>';
    showConfirm('批量删除节点', html, function () {
        var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
        fetch(NODE_LIST_CONFIG.urls.batchDelete, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'
            },
            body: JSON.stringify({ node_ids: nodeIds })
        })
        .then(function (resp) { return resp.json(); })
        .then(function (result) {
            if (result && result.success) {
                if (window.showToast) showToast(result.message || '删除成功', 'success');
                setTimeout(function () { location.reload(); }, 400);
                return;
            }
            showAlert('批量删除失败', (result && result.message) || '操作失败');
        })
        .catch(function () {
            showAlert('批量删除失败', '网络错误或服务器异常');
        });
    }, true, 'lg');
}

/* ========== 节点详情弹窗 ========== */
function openNodeDetail(nodeId) {
    currentDetailNodeId = nodeId;
    if (!nodeDetailModal) nodeDetailModal = new bootstrap.Modal(document.getElementById('nodeDetailModal'));
    nodeDetailModal.show();

    document.getElementById('detailSystemInfo').innerHTML =
        '<div class="text-center text-muted py-2 skeleton-placeholder"><div class="spinner-border spinner-border-sm" role="status"></div><p class="mt-1 small">加载中...</p></div>';
    document.getElementById('detailNginxVersion').textContent = '-';

    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    fetch(NODE_LIST_CONFIG.urls.detail, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ node_id: parseInt(nodeId) })
    })
    .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
    })
    .then(function (result) {
        if (!result.success) {
            document.getElementById('detailHostname').textContent = '加载失败';
            return;
        }
        var info = result.node_info;
        document.getElementById('detailHostname').textContent = info.hostname;
        document.getElementById('detailHostname2').textContent = info.hostname;
        document.getElementById('detailIp').textContent = info.ip;
        document.getElementById('detailPort').textContent = info.port;
        document.getElementById('detailEnvironment').textContent = info.environment;
        document.getElementById('detailStatus').textContent = info.status;
        document.getElementById('detailLocked').innerHTML = (info.is_locked !== undefined ? (info.is_locked ? '<i class="bi bi-lock-fill"></i> 是' : '<i class="bi bi-unlock-fill"></i> 否') : '-');
        document.getElementById('detailCredential').textContent = info.credential_name + ' (' + info.credential_username + ')';
        document.getElementById('detailGroups').textContent = info.groups || '-';
        document.getElementById('detailDescription').textContent = info.description;
        document.getElementById('detailCreatedBy').textContent = info.created_by;
        document.getElementById('detailCreatedAt').textContent = info.created_at;
        document.getElementById('detailUpdatedAt').textContent = info.updated_at;
        document.getElementById('detailLastProbeAt').textContent = info.last_probe_at || '-';
        document.getElementById('detailNginxVersion').textContent = formatNginxVersion(info.nginx_version);
        document.getElementById('detailNginxPath').textContent = info.nginx_path;

        var editUrl = NODE_LIST_CONFIG.urls.editBase.replace('0', info.id);
        document.getElementById('detailEditBtn').setAttribute('href', editUrl);

        var lockBtn = document.getElementById('detailLockBtn');
        if (info.is_locked) {
            lockBtn.innerHTML = '<i class="bi bi-unlock-fill"></i> 解锁';
            lockBtn.className = 'btn btn-outline-warning btn-sm';
        } else {
            lockBtn.innerHTML = '<i class="bi bi-lock"></i> 锁定';
            lockBtn.className = 'btn btn-outline-secondary btn-sm';
        }

        if (info.has_credential) {
            refreshSystemInfo();
        } else {
            document.getElementById('detailSystemInfo').innerHTML = '<p class="text-muted small">未配置SSH凭证或凭证已禁用</p>';
        }
    })
    .catch(function () {
        document.getElementById('detailHostname').textContent = '加载失败';
    });
}

function refreshSystemInfo() {
    if (!currentDetailNodeId) return;
    document.getElementById('detailSystemInfo').innerHTML =
        '<div class="text-center text-muted py-2"><div class="spinner-border spinner-border-sm" role="status"></div><p class="mt-1 small">加载中...</p></div>';
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    fetch(NODE_LIST_CONFIG.urls.systemInfo, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ node_id: parseInt(currentDetailNodeId) })
    })
    .then(function (resp) { return resp.json(); })
    .then(function (result) {
        if (!result.success) {
            document.getElementById('detailSystemInfo').innerHTML = '<div class="text-danger mt-1 small">' + (result.message || '获取失败') + '</div>';
            return;
        }
        if (result.async && result.task_center_id) {
            var pollTimer = setInterval(function() {
                fetch('/releases/tasks/progress/?ids=' + result.task_center_id, {headers:{'X-Requested-With':'XMLHttpRequest'}})
                .then(function(r){return r.json();})
                .then(function(d){
                    if (d.success && d.tasks && d.tasks[0].finished) {
                        clearInterval(pollTimer);
                        if (d.tasks[0].status === 'success') {
                            try {
                                var sysInfo = JSON.parse(d.tasks[0].result);
                                renderSystemInfo(sysInfo);
                            } catch(e) {
                                document.getElementById('detailSystemInfo').innerHTML = '<div class="text-danger mt-1 small">结果解析失败</div>';
                            }
                        } else {
                            document.getElementById('detailSystemInfo').innerHTML = '<div class="text-danger mt-1 small">' + (d.tasks[0].detail || '获取失败') + '</div>';
                        }
                    }
                });
            }, NODE_LIST_CONFIG.sysPollIntervalMs);
        }
    })
    .catch(function () {
        document.getElementById('detailSystemInfo').innerHTML = '<div class="text-danger mt-1 small">网络错误</div>';
    });
}

function updateListNginxVersionCell(nodeId, versionText) {
    var link = document.querySelector('.node-detail-link[data-node-id="' + nodeId + '"]');
    if (!link) return;
    var row = link.closest('tr');
    if (!row) return;
    var cell = row.querySelector('.nginx-version-cell');
    if (!cell) return;
    var available = versionText ? 'true' : 'false';
    cell.setAttribute('data-nginx-version', versionText || '');
    cell.setAttribute('data-nginx-available', available);
    var badgeTd = cell.closest('td');
    if (!badgeTd) return;
    var badge = badgeTd.querySelector('.badge');
    if (!badge) return;
    if (versionText) {
        badge.className = 'badge bg-primary';
        badge.title = '已检测到 Nginx';
        badge.innerHTML = formatNginxVersion(versionText);
    } else {
        badge.className = 'badge bg-warning text-dark';
        badge.title = '未检测到 Nginx';
        badge.innerHTML = '<i class="bi bi-exclamation-triangle"></i> 未检测到';
    }
}

function detectNginxVersion() {
    if (!currentDetailNodeId) return;
    document.getElementById('detailNginxVersion').textContent = '检测中...';
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    fetch(NODE_LIST_CONFIG.urls.nginxVersion, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ node_id: parseInt(currentDetailNodeId) })
    })
    .then(function (resp) { return resp.json(); })
    .then(function (result) {
        if (!result.success) {
            document.getElementById('detailNginxVersion').textContent = result.message || '获取失败';
            return;
        }
        if (result.async && result.task_center_id) {
            var pollTimer = setInterval(function() {
                fetch('/releases/tasks/progress/?ids=' + result.task_center_id, {headers:{'X-Requested-With':'XMLHttpRequest'}})
                .then(function(r){return r.json();})
                .then(function(d){
                    if (d.success && d.tasks && d.tasks[0].finished) {
                        clearInterval(pollTimer);
                        if (d.tasks[0].status === 'success') {
                            var ver = d.tasks[0].result || '';
                            document.getElementById('detailNginxVersion').textContent = formatNginxVersion(ver);
                            updateListNginxVersionCell(currentDetailNodeId, ver);
                        } else {
                            document.getElementById('detailNginxVersion').textContent = d.tasks[0].detail || '获取失败';
                            updateListNginxVersionCell(currentDetailNodeId, '');
                        }
                    }
                });
            }, NODE_LIST_CONFIG.sysPollIntervalMs);
        }
    })
    .catch(function () {
        document.getElementById('detailNginxVersion').textContent = '网络错误';
    });
}

function renderSystemInfo(sysInfo) {
    var container = document.getElementById('detailSystemInfo');
    if (!sysInfo || typeof sysInfo !== 'object') {
        container.innerHTML = '<p class="text-muted small">无信息</p>';
        return;
    }
    var items = [
        { label: '操作系统', value: (sysInfo.os || '').replace(/\s*\([^)]*\)\s*$/, '') },
        { label: '内核版本', value: sysInfo.kernel },
        { label: 'CPU', value: (sysInfo.cpu || '') + (sysInfo.cpu_cores ? ' (' + sysInfo.cpu_cores + ' 核)' : '') },
        { label: '内存', value: (sysInfo.memory_used || '?') + ' / ' + (sysInfo.memory_total || '?') },
        { label: '磁盘', value: (sysInfo.disk_used || '?') + ' / ' + (sysInfo.disk_total || '?') },
        { label: '运行时间', value: sysInfo.uptime },
    ];
    var html = '<table class="table table-sm table-borderless mb-0 small">';
    items.forEach(function (item) {
        if (item.value && item.value !== '未知' && item.value !== '? / ?') {
            html += '<tr><td class="text-muted pe-3" style="width:80px;">' + escapeHtml(item.label) + '</td><td>' + escapeHtml(String(item.value)) + '</td></tr>';
        }
    });
    html += '</table>';
    if (html.indexOf('<tr>') === -1) {
        container.innerHTML = '<p class="text-muted small">无有效信息</p>';
        return;
    }
    container.innerHTML = html;
}

function escapeHtml(text) {
    var d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}