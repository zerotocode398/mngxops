/* ========== 节点组列表页 JS ========== */
/* 依赖 base.html 中的全局函数：submitPostConfirm, bindModalTableRowToggle */

function initGroupTagSearch() {
    const wrapper = document.getElementById('groupCombinedTagWrapper');
    const field = document.getElementById('groupCombinedSearchField');
    const hidden = document.getElementById('groupSearchHidden');
    if (!wrapper || !field || !hidden) return;

    function focusFieldWithCaret() {
        field.focus();
        const len = field.value.length;
        if (typeof field.setSelectionRange === 'function') {
            field.setSelectionRange(len, len);
        }
    }

    function updateHidden() {
        const badges = wrapper.querySelectorAll('.tag-badge');
        const values = [];
        badges.forEach(function (b) {
            values.push(b.getAttribute('data-value'));
        });
        hidden.value = values.join(',');
    }

    function createBadge(text) {
        const badge = document.createElement('span');
        badge.className = 'tag-badge';
        badge.setAttribute('data-value', text);
        badge.innerHTML = text + '<span class="tag-remove">&times;</span>';
        badge.querySelector('.tag-remove').addEventListener('click', function (e) {
            e.stopPropagation();
            badge.remove();
            updateHidden();
            setTimeout(focusFieldWithCaret, 0);
            document.getElementById('groupSearchForm').submit();
        });
        wrapper.insertBefore(badge, field);
        updateHidden();
    }

    const initVal = hidden.value.trim();
    if (initVal) {
        initVal.split(',').map(function (t) { return t.trim(); }).filter(Boolean)
            .forEach(function (tag) { createBadge(tag); });
    }

    field.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            const text = field.value.trim();
            if (text) {
                createBadge(text);
                field.value = '';
                updateHidden();
                setTimeout(focusFieldWithCaret, 0);
                document.getElementById('groupSearchForm').submit();
            }
        }
        if (e.key === 'Backspace' && field.value === '') {
            const badges = wrapper.querySelectorAll('.tag-badge');
            if (badges.length > 0) {
                badges[badges.length - 1].remove();
                updateHidden();
                setTimeout(focusFieldWithCaret, 0);
                document.getElementById('groupSearchForm').submit();
            }
        }
    });

    wrapper.addEventListener('click', function (e) {
        if (e.target === wrapper) {
            focusFieldWithCaret();
        }
    });

    focusFieldWithCaret();
}

function openGroupDeleteModal(el) {
    const groupName = el.getAttribute('data-group-name') || '-';
    const deleteUrl = el.getAttribute('data-delete-url') || '#';
    submitPostConfirm(
        '确认删除',
        '确定要删除节点组 ' + groupName + ' 吗？此操作不可恢复！关联的节点不会删除。',
        deleteUrl
    );
}

function updateSelectedCount(groupId) {
    const modal = document.getElementById('manageNodesModal' + groupId);
    if (!modal) return;

    const checks = modal.querySelectorAll('.node-check:checked');
    const countEl = document.getElementById('selectedCount' + groupId);
    if (countEl) {
        countEl.textContent = String(checks.length);
    }

    const all = modal.querySelector('.manage-select-all');
    if (!all) return;
    const enabledChecks = modal.querySelectorAll('.node-check:not([disabled])');
    if (!enabledChecks.length) {
        all.checked = false;
        all.indeterminate = false;
        return;
    }
    const checkedEnabled = modal.querySelectorAll('.node-check:not([disabled]):checked');
    if (checkedEnabled.length === 0) {
        all.checked = false;
        all.indeterminate = false;
    } else if (checkedEnabled.length === enabledChecks.length) {
        all.checked = true;
        all.indeterminate = false;
    } else {
        all.checked = false;
        all.indeterminate = true;
    }
}

function renderNodeRow(node, currentGroupId, currentNodeIds) {
    var isInGroup = currentNodeIds.indexOf(node.id) >= 0;
    var isFull = node.groups_count >= 3 && !isInGroup;
    var groupsHtml = '';
    if (node.groups && node.groups.length > 0) {
        for (var i = 0; i < node.groups.length; i++) {
            groupsHtml += '<span class="badge bg-info">' + escapeHtmlGrp(node.groups[i].name) + '</span> ';
        }
    } else {
        groupsHtml = '<span class="text-muted">无</span>';
    }
    if (isFull) {
        groupsHtml += '<span class="badge bg-warning">已满</span>';
    }
    var statusLabel = '';
    if (node.status === 'online') {
        statusLabel = '<span class="badge bg-success">在线</span>';
    } else if (node.status === 'offline') {
        statusLabel = '<span class="badge bg-danger">离线</span>';
    } else {
        statusLabel = '<span class="badge bg-secondary">未知</span>';
    }
    var checkboxHtml = '';
    if (isFull) {
        checkboxHtml = '<input type="checkbox" name="node_ids" value="' + node.id + '" class="form-check-input node-check" disabled>';
    } else if (isInGroup) {
        checkboxHtml = '<input type="checkbox" name="node_ids" value="' + node.id + '" class="form-check-input node-check" checked>';
    } else {
        checkboxHtml = '<input type="checkbox" name="node_ids" value="' + node.id + '" class="form-check-input node-check">';
    }
    return '<tr class="manage-node-row" data-hostname="' + escapeHtmlGrp(node.hostname.toLowerCase()) + '" data-ip="' + escapeHtmlGrp(node.ip) + '">' +
        '<td>' + checkboxHtml + '</td>' +
        '<td>' + escapeHtmlGrp(node.hostname) + '</td>' +
        '<td>' + escapeHtmlGrp(node.ip) + '</td>' +
        '<td>' + groupsHtml + '</td>' +
        '<td>' + statusLabel + '</td>' +
        '</tr>';
}

function escapeHtmlGrp(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

var loadedNodeCache = {};

function loadManageNodes(groupId) {
    var tbody = document.getElementById('manageNodeTbody' + groupId);
    if (!tbody) return;
    if (loadedNodeCache[groupId]) {
        return;
    }
    loadedNodeCache[groupId] = true;

    var url = '/nodes/api/search-nodes/';
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (resp) {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.json();
        })
        .then(function (data) {
            if (!data.success || !data.nodes) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-danger">加载节点失败</td></tr>';
                return;
            }
            var modal = document.getElementById('manageNodesModal' + groupId);
            var rawIds = modal ? modal.getAttribute('data-current-node-ids') : '';
            var currentNodeIds = rawIds ? rawIds.split(',').map(Number) : [];
            var rowsHtml = '';
            for (var i = 0; i < data.nodes.length; i++) {
                rowsHtml += renderNodeRow(data.nodes[i], groupId, currentNodeIds);
            }
            if (!data.nodes.length) {
                rowsHtml = '<tr><td colspan="5" class="text-center py-3 text-muted">暂无可用节点</td></tr>';
            }
            tbody.innerHTML = rowsHtml;
            bindModalEvents(groupId);
            updateSelectedCount(groupId);
        })
        .catch(function () {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-danger">加载节点失败，请重试</td></tr>';
        });
}

function bindModalEvents(groupId) {
    var modal = document.getElementById('manageNodesModal' + groupId);
    if (!modal) return;
    var checks = modal.querySelectorAll('.node-check');
    var selectAll = modal.querySelector('.manage-select-all');
    var searchInput = modal.querySelector('.manage-node-search');
    var rows = modal.querySelectorAll('.manage-node-row');

    if (selectAll) {
        var newSelectAll = selectAll.cloneNode(true);
        selectAll.parentNode.replaceChild(newSelectAll, selectAll);
        newSelectAll.addEventListener('change', function () {
            var visibleChecks = modal.querySelectorAll('.manage-node-row:not([style*="display: none"]) .node-check:not([disabled])');
            visibleChecks.forEach(function (cb) { cb.checked = newSelectAll.checked; });
            updateSelectedCount(groupId);
        });
    }

    checks.forEach(function (cb) {
        var newCb = cb.cloneNode(true);
        cb.parentNode.replaceChild(newCb, cb);
        newCb.addEventListener('change', function () { updateSelectedCount(groupId); });
    });

    if (searchInput) {
        var newSearch = searchInput.cloneNode(true);
        searchInput.parentNode.replaceChild(newSearch, searchInput);
        newSearch.addEventListener('input', function () {
            var q = this.value.trim().toLowerCase();
            var allRows = modal.querySelectorAll('.manage-node-row');
            allRows.forEach(function (row) {
                var h = row.getAttribute('data-hostname') || '';
                var ip = row.getAttribute('data-ip') || '';
                row.style.display = (!q || h.indexOf(q) >= 0 || ip.indexOf(q) >= 0) ? '' : 'none';
            });
        });
    }

    var tbody = modal.querySelector('#manageNodeTbody' + groupId);
    if (tbody) {
        bindModalTableRowToggle(tbody, '.node-check');
    }
}

function initManageNodeModal(groupId) {
    var modal = document.getElementById('manageNodesModal' + groupId);
    if (!modal) return;
    modal.addEventListener('shown.bs.modal', function () {
        loadManageNodes(groupId);
        updateSelectedCount(groupId);
        var searchInput = modal.querySelector('.manage-node-search');
        if (searchInput) searchInput.focus();
    });
    updateSelectedCount(groupId);
}

document.addEventListener('DOMContentLoaded', function () {
    initGroupTagSearch();
    document.querySelectorAll('.manage-select-all').forEach(function (el) {
        const groupId = el.getAttribute('data-group-id');
        if (groupId) initManageNodeModal(groupId);
    });
});