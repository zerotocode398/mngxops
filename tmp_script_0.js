
/** 当前向导步骤 1-4 */
let currentStep = 1;
/** 已确认选中的节点 map: id -> node */
let selectedNodes = {};
/** 弹窗临时勾选 map */
let modalPickedNodes = {};
/** 各节点 nginx -V 缓存 */
let nodeNginxCache = {};
/** 各节点最终 configure 预览缓存 */
let nodeTargetOptsCache = {};
/** 参考节点 id */
let refNodeId = null;
/** 多节点编译参数是否一致 */
let paramsConsistent = true;
/** 已确认的模块增减（内存状态） */
let pendingRemovedModules = [];
let pendingAddedModules = [];
/** 弹窗草稿勾选（确认前） */
let draftRemovedModules = [];
let draftAddedModules = [];
/** 弹窗打开时的基线参数（trim 后） */
let modalBaselineParams = [];
let upgradeSelectNodeModal = null;
let paramsMismatchModal = null;
let moduleAdjustModal = null;
let currentTaskIds = [];
let pollTimer = null;
let currentBatchNumber = '';
/** 最近一次弹窗搜索结果，供全选使用 */
let lastSearchNodes = [];

/**
 * 官方全部可写入 configure 的模块相关参数
 * - --with-*：默认不编译，勾选后追加启用
 * - --without-*：默认已编译，勾选后追加以禁用
 * 来源：https://nginx.org/en/docs/configure.html + nginx auto/options
 */
const BUILTIN_ADD_MODULES = [
    // ── 事件 / IO ──
    '--with-select_module',
    '--with-poll_module',
    '--with-threads',
    '--with-file-aio',
    '--without-select_module',
    '--without-poll_module',
    '--without-quic_bpf_module',
    // ── HTTP 可选模块（静态 / 动态）──
    '--with-http_ssl_module',
    '--with-http_v2_module',
    '--with-http_v3_module',
    '--with-http_realip_module',
    '--with-http_addition_module',
    '--with-http_xslt_module',
    '--with-http_xslt_module=dynamic',
    '--with-http_image_filter_module',
    '--with-http_image_filter_module=dynamic',
    '--with-http_geoip_module',
    '--with-http_geoip_module=dynamic',
    '--with-http_sub_module',
    '--with-http_dav_module',
    '--with-http_flv_module',
    '--with-http_mp4_module',
    '--with-http_gunzip_module',
    '--with-http_gzip_static_module',
    '--with-http_auth_request_module',
    '--with-http_random_index_module',
    '--with-http_secure_link_module',
    '--with-http_degradation_module',
    '--with-http_slice_module',
    '--with-http_stub_status_module',
    '--with-http_perl_module',
    '--with-http_perl_module=dynamic',
    // ── HTTP 默认模块（勾选写入 --without 以禁用）──
    '--without-http_charset_module',
    '--without-http_gzip_module',
    '--without-http_ssi_module',
    '--without-http_userid_module',
    '--without-http_access_module',
    '--without-http_auth_basic_module',
    '--without-http_mirror_module',
    '--without-http_autoindex_module',
    '--without-http_geo_module',
    '--without-http_map_module',
    '--without-http_split_clients_module',
    '--without-http_referer_module',
    '--without-http_rewrite_module',
    '--without-http_proxy_module',
    '--without-http_fastcgi_module',
    '--without-http_uwsgi_module',
    '--without-http_scgi_module',
    '--without-http_grpc_module',
    '--without-http_tunnel_module',
    '--without-http_memcached_module',
    '--without-http_limit_conn_module',
    '--without-http_limit_req_module',
    '--without-http_empty_gif_module',
    '--without-http_browser_module',
    '--without-http_upstream_hash_module',
    '--without-http_upstream_ip_hash_module',
    '--without-http_upstream_least_conn_module',
    '--without-http_upstream_least_time_module',
    '--without-http_upstream_random_module',
    '--without-http_upstream_keepalive_module',
    '--without-http_upstream_zone_module',
    '--without-http_upstream_sticky_module',
    '--without-http',
    '--without-http-cache',
    // ── Mail ──
    '--with-mail',
    '--with-mail=dynamic',
    '--with-mail_ssl_module',
    '--without-mail_pop3_module',
    '--without-mail_imap_module',
    '--without-mail_smtp_module',
    // ── Stream（TCP/UDP）──
    '--with-stream',
    '--with-stream=dynamic',
    '--with-stream_ssl_module',
    '--with-stream_realip_module',
    '--with-stream_geoip_module',
    '--with-stream_geoip_module=dynamic',
    '--with-stream_ssl_preread_module',
    '--without-stream_limit_conn_module',
    '--without-stream_access_module',
    '--without-stream_geo_module',
    '--without-stream_map_module',
    '--without-stream_split_clients_module',
    '--without-stream_return_module',
    '--without-stream_pass_module',
    '--without-stream_set_module',
    '--without-stream_upstream_hash_module',
    '--without-stream_upstream_least_conn_module',
    '--without-stream_upstream_least_time_module',
    '--without-stream_upstream_random_module',
    '--without-stream_upstream_zone_module',
    // ── 其他 ──
    '--with-compat',
    '--with-pcre',
    '--with-pcre-jit',
    '--with-libatomic',
    '--with-debug',
    '--with-google_perftools_module',
    '--with-cpp_test_module',
    '--without-pcre',
    '--without-pcre2'
];

/** 判断基线是否已含该参数（--with 静态/动态互斥；--without 精确匹配） */
function isBuiltinModulePresent(paramSet, mod) {
    if (!mod) return false;
    if (paramSet[mod]) return true;
    // --with-xxx 与 --with-xxx=dynamic 视为同一可选模块
    if (String(mod).indexOf('--with-') === 0) {
        var base = String(mod).replace(/=dynamic$/, '');
        if (paramSet[base] || paramSet[base + '=dynamic']) return true;
    }
    return false;
}

function getCSRFToken() {
    return document.querySelector('[name=csrfmiddlewaretoken]').value;
}

/** 去掉 nginx/ 前缀展示版本号 */
function formatNginxVer(str) {
    if (!str) return '';
    return String(str).trim().replace(/^nginx[/\\-]/i, '');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

/** 将参数列表渲染为高亮分行 HTML */
function renderParamsHighlight(params, leading) {
    var list = params || [];
    if (!list.length) {
        return '<span class="text-muted">无编译参数</span>';
    }
    var html = '<div class="nginx-v-params">';
    if (leading) html += '<div>' + escapeHtml(leading) + '</div>';
    list.forEach(function (p) {
        html += '<div><span class="param-highlight">' + escapeHtml(p) + '</span></div>';
    });
    html += '</div>';
    return html;
}

/**
 * 带增减批注的参数预览（仅展示，不影响实际 opts）
 * @param {string[]} baselineParams 参考节点基线
 * @param {string[]} finalTokens 估算/实际最终 token
 * @param {object} deltas 增减增量
 * @param {string} [leading] 引导行文案
 */
function renderFinalParamsWithAnnotations(baselineParams, finalTokens, deltas, leading) {
    var lead = leading || './configure \\';
    var removedSet = {};
    (deltas.removedModules || []).forEach(function (p) { removedSet[p] = true; });
    var addedSet = {};
    (deltas.addedModules || []).forEach(function (p) { addedSet[p] = true; });

    var hasChange = (deltas.removedModules && deltas.removedModules.length) ||
        (deltas.addedModules && deltas.addedModules.length) ||
        (deltas.thirdParty && deltas.thirdParty.length);

    var tokensForPlain = (finalTokens && finalTokens.length) ? finalTokens : (baselineParams || []);
    if (!hasChange) {
        return renderParamsHighlight(tokensForPlain, lead);
    }

    var html = '<div class="nginx-v-params">';
    html += '<div>' + escapeHtml(lead) + '</div>';

    (baselineParams || []).forEach(function (p) {
        if (removedSet[p]) {
            html += '<div><span class="param-anno-removed">- ' + escapeHtml(p) + '</span>' +
                '<span class="param-anno-tag tag-removed">已移除</span></div>';
        } else {
            html += '<div><span class="param-highlight">' + escapeHtml(p) + '</span></div>';
        }
    });

    (deltas.addedModules || []).forEach(function (p) {
        html += '<div><span class="param-anno-added">+ ' + escapeHtml(p) + '</span>' +
            '<span class="param-anno-tag tag-added">新增</span></div>';
    });
    (deltas.thirdParty || []).forEach(function (tp) {
        var label = '--add-module=&lt;' + escapeHtml(tp.name || 'module') + '&gt;';
        html += '<div><span class="param-anno-added">+ ' + label + '</span>' +
            '<span class="param-anno-tag tag-added">新增</span></div>';
    });

    // 兜底：最终 opts 中既不在基线、也不在 added 列表的 token（如 switch_path 重写的 --prefix）
    var baselineSet = {};
    (baselineParams || []).forEach(function (p) { baselineSet[p] = true; });
    (finalTokens || []).forEach(function (t) {
        if (baselineSet[t] || addedSet[t]) return;
        if (String(t).indexOf('--add-module=') === 0) return;
        html += '<div><span class="param-highlight">' + escapeHtml(t) + '</span></div>';
    });

    html += '</div>';
    return html;
}

/** 刷新 Step3 上方编译参数区（含增减醒目标注） */
function refreshAnnotatedConfigOutput() {
    var info = nodeNginxCache[String(refNodeId)];
    if (!info || info._error) return;
    var baseline = info.params || [];
    var deltas = getModuleDeltas();
    var removedSet = {};
    (deltas.removedModules || []).forEach(function (p) { removedSet[p] = true; });
    var finalTokens = baseline.filter(function (p) { return !removedSet[p]; });
    (deltas.addedModules || []).forEach(function (p) {
        if (finalTokens.indexOf(p) < 0) finalTokens.push(p);
    });
    document.getElementById('currentConfigOutput').innerHTML =
        renderFinalParamsWithAnnotations(baseline, finalTokens, deltas, 'configure arguments:');
}

/** 将 configure 字符串拆成 token 列表（支持引号内空格，与后端 shlex 对齐） */
function tokenizeOpts(optsStr) {
    if (!optsStr) return [];
    var flat = String(optsStr).replace(/\\\s*\n\s*/g, ' ').replace(/\s+/g, ' ').trim();
    var tokens = [];
    var i = 0;
    var n = flat.length;
    while (i < n) {
        while (i < n && /\s/.test(flat.charAt(i))) i++;
        if (i >= n) break;
        if (flat.charAt(i) !== '-' || flat.charAt(i + 1) !== '-') {
            i++;
            continue;
        }
        var start = i;
        i += 2;
        while (i < n && /[\w\-]/.test(flat.charAt(i))) i++;
        if (i < n && flat.charAt(i) === '=') {
            i++;
            var q = flat.charAt(i);
            if (q === "'" || q === '"') {
                i++;
                while (i < n && flat.charAt(i) !== q) {
                    if (flat.charAt(i) === '\\' && i + 1 < n) {
                        i += 2;
                        continue;
                    }
                    i++;
                }
                if (i < n && flat.charAt(i) === q) i++;
            } else {
                while (i < n && !/\s/.test(flat.charAt(i))) i++;
            }
        }
        var raw = flat.substring(start, i);
        // 去掉 = 后外壳引号，与后端 shlex 行为一致
        var eq = raw.indexOf('=');
        if (eq >= 0) {
            var key = raw.substring(0, eq);
            var val = raw.substring(eq + 1);
            if (val.length >= 2 && (val.charAt(0) === "'" || val.charAt(0) === '"') &&
                val.charAt(0) === val.charAt(val.length - 1)) {
                val = val.substring(1, val.length - 1);
            }
            tokens.push(key + '=' + val);
        } else {
            tokens.push(raw);
        }
    }
    return tokens;
}

/** 将单个 token 格式化为可展示/拼装的 configure 参数 */
function formatConfigureToken(token) {
    if (!token) return '';
    var eq = token.indexOf('=');
    if (eq < 0) return token;
    var key = token.substring(0, eq);
    var val = token.substring(eq + 1);
    if (/[\s"'\\$`|&;<>()]/.test(val)) {
        return key + "='" + String(val).replace(/'/g, "'\\''") + "'";
    }
    return token;
}

/** 将 token 列表安全拼接为多行 configure 参数 */
function joinConfigureOpts(tokens) {
    return (tokens || []).filter(Boolean).map(formatConfigureToken).join(' \\\n    ');
}

/** 规范化 params 签名（排序后拼接，忽略顺序差异） */
function paramsSignature(params) {
    return (params || []).slice().sort().join('\n');
}

function getSelectedPackage() {
    var radio = document.querySelector('.pkg-radio:checked');
    if (!radio) return null;
    // 被过滤隐藏的行不算已选
    var row = radio.closest('.pkg-row');
    if (row && row.style.display === 'none') return null;
    return {
        id: radio.value,
        version: radio.dataset.version || '',
        name: radio.dataset.name || '',
    };
}

function getUpgradeMode() {
    var el = document.querySelector('input[name="upgradeMode"]:checked');
    return el ? el.value : 'upgrade';
}

function getSelectedNodeList() {
    return Object.keys(selectedNodes).map(function (k) { return selectedNodes[k]; });
}

function getModuleDeltas() {
    var thirdParty = [];
    document.querySelectorAll('.tp-module-row').forEach(function (row) {
        var item = collectOneThirdPartyRow(row);
        if (item) thirdParty.push(item);
    });
    return {
        addedModules: pendingAddedModules.slice(),
        removedModules: pendingRemovedModules.slice(),
        thirdParty: thirdParty
    };
}

/** 按升级模式同步目标 --prefix 显隐 */
function syncTargetPrefixVisibility() {
    var wrap = document.getElementById('targetPrefixWrap');
    var input = document.getElementById('targetPrefix');
    if (!wrap || !input) return;
    if (getUpgradeMode() === 'switch_path') {
        wrap.classList.remove('d-none');
    } else {
        wrap.classList.add('d-none');
        input.value = '';
    }
}

/**
 * 按与后端一致的规则改写预览用 configure 的 --prefix
 * @param {string} optsStr 当前 target_opts
 * @param {string} nodePrefix 节点自身 prefix
 */
function applyPrefixToTargetOpts(optsStr, nodePrefix) {
    var mode = getUpgradeMode();
    var tokens = tokenizeOpts(optsStr);
    if (mode !== 'switch_path') {
        return tokens.length ? joinConfigureOpts(tokens) : (optsStr || '');
    }
    var targetPrefix = (document.getElementById('targetPrefix').value || '').trim();
    if (!targetPrefix) {
        return tokens.length ? joinConfigureOpts(tokens) : (optsStr || '');
    }
    var newTokens = [];
    var hasPrefix = false;
    tokens.forEach(function (t) {
        if (t.indexOf('--prefix=') === 0) {
            newTokens.push('--prefix=' + targetPrefix);
            hasPrefix = true;
        } else {
            newTokens.push(t);
        }
    });
    if (!hasPrefix) newTokens.unshift('--prefix=' + targetPrefix);
    return joinConfigureOpts(newTokens);
}

// ========== 向导步骤 ==========
function goStep(step) {
    if (step === 2 && !canLeaveStep1()) return;
    if (step === 3) {
        if (!canLeaveStep1()) return;
        syncTargetPrefixVisibility();
        showWizardStep(3);
        fetchAllNginxV();
        return;
    }
    if (step === 4) {
        previewAndGoStep4();
        return;
    }
    showWizardStep(step);
}

function canLeaveStep1() {
    var nodes = getSelectedNodeList();
    if (!nodes.length) {
        if (window.showToast) showToast('请先选择目标节点', 'warning');
        return false;
    }
    if (!getSelectedPackage()) {
        if (window.showToast) showToast('请先选择源码包', 'warning');
        return false;
    }
    return true;
}

function showWizardStep(step) {
    currentStep = step;
    document.querySelectorAll('.wizard-panel').forEach(function (el) { el.classList.add('d-none'); });
    var panel = document.getElementById('step' + step);
    if (panel) panel.classList.remove('d-none');

    document.querySelectorAll('.wizard-step-item').forEach(function (el) {
        var s = parseInt(el.getAttribute('data-step'), 10);
        el.classList.remove('active', 'done');
        if (s === step) el.classList.add('active');
        else if (s < step) el.classList.add('done');
    });
    saveUpgradeWizardState();
}

function updateStep1NextBtn() {
    var ok = getSelectedNodeList().length > 0 && !!getSelectedPackage();
    document.getElementById('btnStep1Next').disabled = !ok;
    document.getElementById('step1NodeCount').textContent = String(getSelectedNodeList().length);
}

// ========== 源码包 ==========
function initPackageTable() {
    document.querySelectorAll('.pkg-row').forEach(function (row) {
        row.addEventListener('click', function (e) {
            if (e.target && e.target.tagName === 'A') return;
            if (row.style.display === 'none') return;
            var radio = row.querySelector('.pkg-radio');
            if (!radio) return;
            radio.checked = true;
            document.querySelectorAll('.pkg-row').forEach(function (r) { r.classList.remove('selected'); });
            row.classList.add('selected');
            updateStep1NextBtn();
        });
    });
    document.querySelectorAll('.pkg-radio').forEach(function (r) {
        r.addEventListener('change', updateStep1NextBtn);
    });
}

/** 初始化源码包条件标签查询 */
function initPackageTagInput() {
    var wrapper = document.getElementById('pkgTagWrapper');
    var field = document.getElementById('pkgSearchField');
    var hidden = document.getElementById('pkgSearchHidden');
    if (!wrapper || !field || !hidden) return;

    function updateHidden() {
        var values = [];
        wrapper.querySelectorAll('.tag-badge').forEach(function (b) {
            values.push(b.getAttribute('data-value'));
        });
        hidden.value = values.join(',');
    }

    function createBadge(text) {
        if (!text) return;
        if (wrapper.querySelector('.tag-badge[data-value="' + text + '"]')) return;
        var badge = document.createElement('span');
        badge.className = 'tag-badge';
        badge.setAttribute('data-value', text);
        badge.innerHTML = text + '<span class="tag-remove">&times;</span>';
        badge.querySelector('.tag-remove').addEventListener('click', function (e) {
            e.stopPropagation();
            badge.remove();
            updateHidden();
            filterPackageTable();
        });
        wrapper.insertBefore(badge, field);
        updateHidden();
    }

    field.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            var val = field.value.trim();
            if (val) {
                createBadge(val);
                field.value = '';
                filterPackageTable();
            }
        } else if (e.key === 'Backspace' && !field.value) {
            var badges = wrapper.querySelectorAll('.tag-badge');
            if (badges.length) {
                badges[badges.length - 1].remove();
                updateHidden();
                filterPackageTable();
            }
        }
    });

    var searchTimer = null;
    field.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
            var live = field.value.trim();
            var base = [];
            wrapper.querySelectorAll('.tag-badge').forEach(function (b) {
                base.push(b.getAttribute('data-value'));
            });
            if (live) base.push(live);
            hidden.value = base.join(',');
            filterPackageTable();
        }, 300);
    });

    wrapper.addEventListener('click', function (e) {
        if (e.target === wrapper) field.focus();
    });
}

/** 按标签 AND 过滤源码包表行（匹配名称或版本） */
function filterPackageTable() {
    var hidden = document.getElementById('pkgSearchHidden');
    var tags = (hidden && hidden.value ? hidden.value.split(',') : [])
        .map(function (t) { return t.trim().toLowerCase(); })
        .filter(Boolean);
    var visible = 0;
    document.querySelectorAll('.pkg-row').forEach(function (row) {
        var name = (row.getAttribute('data-name') || '').toLowerCase();
        var version = (row.getAttribute('data-version') || '').toLowerCase();
        var ok = tags.every(function (q) {
            return name.indexOf(q) >= 0 || version.indexOf(q) >= 0;
        });
        row.style.display = ok ? '' : 'none';
        if (ok) {
            visible++;
        } else {
            var radio = row.querySelector('.pkg-radio');
            if (radio && radio.checked) {
                radio.checked = false;
                row.classList.remove('selected');
            }
        }
    });
    var wrap = document.querySelector('.pkg-table-wrap');
    var hint = document.getElementById('pkgEmptyHint');
    if (wrap) wrap.style.display = visible ? '' : 'none';
    if (hint) {
        if (visible) hint.classList.remove('show');
        else hint.classList.add('show');
    }
    updateStep1NextBtn();
}

// ========== 节点多选弹窗 ==========
function initUpgradeNodeTagInput() {
    var wrapper = document.getElementById('upgradeNodeTagWrapper');
    var field = document.getElementById('upgradeNodeSearchField');
    var hidden = document.getElementById('upgradeNodeSearchHidden');

    function focusField() { field.focus(); }

    function updateHidden() {
        var values = [];
        wrapper.querySelectorAll('.tag-badge').forEach(function (b) {
            values.push(b.getAttribute('data-value'));
        });
        hidden.value = values.join(',');
    }

    function createBadge(text) {
        if (!text) return;
        if (wrapper.querySelector('.tag-badge[data-value="' + text + '"]')) return;
        var badge = document.createElement('span');
        badge.className = 'tag-badge';
        badge.setAttribute('data-value', text);
        badge.innerHTML = text + '<span class="tag-remove">&times;</span>';
        badge.querySelector('.tag-remove').addEventListener('click', function (e) {
            e.stopPropagation();
            badge.remove();
            updateHidden();
            doUpgradeNodeSearch();
        });
        wrapper.insertBefore(badge, field);
        updateHidden();
    }

    field.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            var val = field.value.trim();
            if (val) {
                createBadge(val);
                field.value = '';
                doUpgradeNodeSearch();
            }
        } else if (e.key === 'Backspace' && !field.value) {
            var badges = wrapper.querySelectorAll('.tag-badge');
            if (badges.length) {
                badges[badges.length - 1].remove();
                updateHidden();
                doUpgradeNodeSearch();
            }
        }
    });

    var searchTimer = null;
    field.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
            var live = field.value.trim();
            var base = [];
            wrapper.querySelectorAll('.tag-badge').forEach(function (b) {
                base.push(b.getAttribute('data-value'));
            });
            if (live) base.push(live);
            hidden.value = base.join(',');
            doUpgradeNodeSearch();
        }, 300);
    });

    wrapper.addEventListener('click', function (e) {
        if (e.target === wrapper) focusField();
    });
}

function updateModalPickedCount() {
    document.getElementById('modalPickedCount').textContent = String(Object.keys(modalPickedNodes).length);
}

function doUpgradeNodeSearch() {
    var search = document.getElementById('upgradeNodeSearchHidden').value || '';
    var tbody = document.getElementById('upgradeSearchResultTbody');
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">' +
        '<div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>搜索中...</td></tr>';

    fetch('/nodes/api/search-nodes/?search=' + encodeURIComponent(search), {
        method: 'GET',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function (resp) { return resp.json(); })
    .then(function (result) {
        lastSearchNodes = result.nodes || [];
        document.getElementById('upgradeResultCount').textContent = lastSearchNodes.length;
        if (!lastSearchNodes.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">' +
                '<i class="bi bi-inbox"></i><p class="mt-1 mb-0">未找到匹配的节点</p></td></tr>';
            return;
        }

        var html = '';
        lastSearchNodes.forEach(function (node) {
            var selectable = node.status === 'online' && node.has_credential;
            var checked = modalPickedNodes[String(node.id)] ? ' checked' : '';
            if (!selectable) checked = '';

            var statusBadge = '';
            if (node.status === 'online') statusBadge = '<span class="badge bg-success">在线</span>';
            else if (node.status === 'offline') statusBadge = '<span class="badge bg-danger">离线</span>';
            else statusBadge = '<span class="badge bg-secondary">未知</span>';
            if (!node.has_credential) statusBadge += ' <span class="badge bg-warning text-dark">无凭证</span>';

            var groupBadges = '';
            if (node.groups && node.groups.length) {
                node.groups.forEach(function (g) {
                    groupBadges += '<span class="badge bg-info text-dark me-1">' + escapeHtml(g.name) + '</span>';
                });
            } else {
                groupBadges = '<span class="text-muted small">-</span>';
            }

            html += '<tr class="' + (selectable ? '' : 'disabled-row') + '" data-node-id="' + node.id + '"' +
                (selectable ? '' : ' data-disabled="1"') + '>' +
                '<td><input type="checkbox" class="upgrade-node-cb" value="' + node.id + '"' + checked +
                (selectable ? '' : ' disabled') + '></td>' +
                '<td>' + escapeHtml(node.hostname) + '</td>' +
                '<td><small>' + escapeHtml(node.ip) + ':' + escapeHtml(String(node.port || 22)) + '</small></td>' +
                '<td>' + groupBadges + '</td>' +
                '<td>' + statusBadge + '</td></tr>';
        });
        tbody.innerHTML = html;

        tbody.querySelectorAll('tr[data-node-id]').forEach(function (tr) {
            if (tr.getAttribute('data-disabled') === '1') return;
            var nodeId = tr.getAttribute('data-node-id');
            var node = lastSearchNodes.find(function (n) { return String(n.id) === String(nodeId); });
            var cb = tr.querySelector('.upgrade-node-cb');
            if (cb) {
                cb.addEventListener('change', function () {
                    toggleModalPick(node, cb.checked);
                });
            }
        });
        // 弹窗表格：点击行切换勾选
        bindModalTableRowToggle(tbody, '.upgrade-node-cb');
        updateModalPickedCount();
    })
    .catch(function () {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger py-4">搜索失败</td></tr>';
    });
}

function toggleModalPick(node, checked) {
    if (!node) return;
    var key = String(node.id);
    if (checked) {
        modalPickedNodes[key] = {
            id: node.id,
            hostname: node.hostname,
            ip: node.ip,
            port: node.port,
            groups: node.groups || [],
            group_names: (node.groups || []).map(function (g) { return g.name; }),
        };
    } else {
        delete modalPickedNodes[key];
    }
    updateModalPickedCount();
}

function confirmUpgradeNodeSelection() {
    selectedNodes = {};
    Object.keys(modalPickedNodes).forEach(function (k) {
        selectedNodes[k] = modalPickedNodes[k];
    });
    // 清理已移除节点的缓存
    Object.keys(nodeNginxCache).forEach(function (k) {
        if (!selectedNodes[k]) delete nodeNginxCache[k];
    });
    renderSelectedNodes();
    updateStep1NextBtn();
    saveUpgradeWizardState();
    var modalEl = document.getElementById('upgradeSelectNodeModal');
    var inst = bootstrap.Modal.getInstance(modalEl);
    if (inst) inst.hide();
}

/** 渲染已选节点 chips（不展示 nginx 版本） */
function renderSelectedNodes() {
    var display = document.getElementById('selectedNodeDisplay');
    var nodes = getSelectedNodeList();
    var label = document.getElementById('btnSelectNodeLabel');
    if (!nodes.length) {
        display.innerHTML = '<span class="text-muted small" id="noSelectedNode">请选择一个或多个目标节点</span>';
        label.textContent = '选择节点';
        return;
    }
    label.textContent = '重新选择';
    var html = '';
    nodes.forEach(function (n, idx) {
        var groups = '';
        (n.group_names || []).forEach(function (g) {
            groups += '<span class="badge bg-info text-dark">' + escapeHtml(g) + '</span>';
        });
        var refCls = (idx === 0) ? ' ref-node' : '';
        var failCls = (nodeNginxCache[String(n.id)] && nodeNginxCache[String(n.id)]._error) ? ' fetch-fail' : '';
        html += '<span class="selected-node-chip' + refCls + failCls + '" data-id="' + n.id + '">' +
            '<span class="node-identity">' + escapeHtml(n.hostname) +
            '<small class="text-muted">(' + escapeHtml(n.ip) + ':' + escapeHtml(String(n.port || 22)) + ')</small></span>' +
            groups +
            '<span class="chip-remove" title="移除" onclick="removeSelectedNode(' + n.id + ')">&times;</span>' +
            '</span>';
    });
    display.innerHTML = html;
}

function removeSelectedNode(nodeId) {
    delete selectedNodes[String(nodeId)];
    delete nodeNginxCache[String(nodeId)];
    delete modalPickedNodes[String(nodeId)];
    renderSelectedNodes();
    updateStep1NextBtn();
    saveUpgradeWizardState();
}

// ========== nginx -V ==========
/** 拉取各节点 nginx -V；返回 Promise
 * @param {boolean} [skipMismatchModal] 恢复向导时跳过不一致弹窗
 */
function fetchAllNginxV(skipMismatchModal) {
    var nodes = getSelectedNodeList();
    if (!nodes.length) {
        if (window.showToast) showToast('请先选择目标节点', 'warning');
        return Promise.resolve({ ok: false });
    }
    var btn = document.getElementById('btnFetchConfig');
    btn.disabled = true;
    document.getElementById('fetchStatus').innerHTML =
        '<span class="text-warning"><span class="spinner-border spinner-border-sm"></span> 正在读取 ' +
        nodes.length + ' 台节点的 nginx -V...</span>';
    document.getElementById('btnStep3Next').disabled = true;
    document.getElementById('currentConfigBlock').classList.add('d-none');

    var promises = nodes.map(function (node) {
        return fetch(`{% url 'upgrade:api_nginx_v' 0 %}`.replace('0', node.id), {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken(), 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success) {
                nodeNginxCache[String(node.id)] = { _error: data.message || '获取失败' };
                return { id: node.id, ok: false, message: data.message };
            }
            nodeNginxCache[String(node.id)] = data.data;
            return { id: node.id, ok: true };
        })
        .catch(function (err) {
            nodeNginxCache[String(node.id)] = { _error: err.message || '网络错误' };
            return { id: node.id, ok: false, message: err.message };
        });
    });

    return Promise.all(promises).then(function (results) {
        btn.disabled = false;
        var okCount = results.filter(function (r) { return r.ok; }).length;
        var failCount = results.length - okCount;
        if (failCount === 0) {
            document.getElementById('fetchStatus').innerHTML =
                '<span class="text-success"><i class="bi bi-check-circle"></i> 全部 ' + okCount + ' 台获取成功</span>';
        } else {
            document.getElementById('fetchStatus').innerHTML =
                '<span class="text-warning"><i class="bi bi-exclamation-triangle"></i> 成功 ' +
                okCount + ' / 失败 ' + failCount + '（失败节点可移除后继续）</span>';
        }
        renderNodeFetchSummary();
        renderSelectedNodes();

        var okNodes = getOkNginxNodes();
        if (!okNodes.length) {
            document.getElementById('currentConfigBlock').classList.add('d-none');
            document.getElementById('btnStep3Next').disabled = true;
            if (window.showToast) showToast('所有节点读取 nginx -V 失败', 'danger');
            return { ok: false };
        }

        paramsConsistent = areParamsConsistent(okNodes);
        if (!refNodeId || !selectedNodes[String(refNodeId)]) {
            refNodeId = okNodes[0].id;
        }

        if (!paramsConsistent && okNodes.length > 1 && !skipMismatchModal) {
            showParamsMismatchModal(okNodes);
        } else {
            openConfigEditor(paramsConsistent);
        }
        return { ok: true, okNodes: okNodes };
    });
}

/** 返回 nginx -V 获取成功的节点列表 */
function getOkNginxNodes() {
    return getSelectedNodeList().filter(function (n) {
        var c = nodeNginxCache[String(n.id)];
        return c && !c._error;
    });
}

/** 判断多节点编译参数是否一致（排序后签名） */
function areParamsConsistent(okNodes) {
    if (!okNodes || okNodes.length <= 1) return true;
    var first = paramsSignature(nodeNginxCache[String(okNodes[0].id)].params);
    for (var i = 1; i < okNodes.length; i++) {
        var sig = paramsSignature(nodeNginxCache[String(okNodes[i].id)].params);
        if (sig !== first) return false;
    }
    return true;
}

/** 展示参数不一致风险弹窗（节点摘要 + 仅差异项） */
function showParamsMismatchModal(okNodes) {
    var tbody = document.getElementById('paramsMismatchTbody');
    var html = '';
    okNodes.forEach(function (n) {
        var cache = nodeNginxCache[String(n.id)] || {};
        var params = cache.params || [];
        var prefix = cache.prefix || '-';
        html += '<tr class="mismatch-node-row" data-node-id="' + n.id + '" onclick="toggleMismatchExpand(this)">' +
            '<td><div class="node-info-cell"><span class="node-identity">' +
            escapeHtml(n.hostname) + '<small class="text-muted">(' + escapeHtml(n.ip) + ')</small>' +
            '</span></div></td>' +
            '<td><code class="code-font">' + escapeHtml(formatNginxVer(cache.version) || '-') + '</code></td>' +
            '<td><span class="code-font text-truncate d-inline-block" style="max-width:100%;" title="' +
            escapeHtml(prefix) + '">' + escapeHtml(prefix) + '</span></td>' +
            '<td>' + params.length +
            ' <i class="bi bi-chevron-down text-muted small"></i></td></tr>' +
            '<tr class="mismatch-expand-row d-none" data-expand-for="' + n.id + '">' +
            '<td colspan="4"><div class="nginx-v-output" style="max-height:160px;">' +
            renderParamsHighlight(params, 'configure arguments:') +
            '</div></td></tr>';
    });
    tbody.innerHTML = html;

    var diffItems = computeParamsDiff(okNodes);
    var countEl = document.getElementById('paramsDiffCount');
    countEl.textContent = diffItems.length ? ' · 共 ' + diffItems.length + ' 项' : '';
    var listEl = document.getElementById('paramsDiffList');
    if (!diffItems.length) {
        listEl.innerHTML = '<p class="text-muted small mb-0">未解析到具体差异项</p>';
    } else {
        var diffHtml = '';
        diffItems.forEach(function (item) {
            var hasBadges = item.hasHosts.map(function (h) {
                return '<span class="badge bg-success me-1">' + escapeHtml(h) + '</span>';
            }).join('');
            var missBadges = item.missHosts.map(function (h) {
                return '<span class="badge bg-secondary me-1">' + escapeHtml(h) + '</span>';
            }).join('');
            diffHtml += '<div class="mismatch-diff-item">' +
                '<div class="diff-param">' + escapeHtml(item.param) + '</div>' +
                '<div class="mb-1"><span class="text-muted">有: </span>' + (hasBadges || '<span class="text-muted">-</span>') + '</div>' +
                '<div><span class="text-muted">无: </span>' + (missBadges || '<span class="text-muted">-</span>') + '</div>' +
                '</div>';
        });
        listEl.innerHTML = diffHtml;
    }

    if (!paramsMismatchModal) {
        paramsMismatchModal = new bootstrap.Modal(document.getElementById('paramsMismatchModal'));
    }
    paramsMismatchModal.show();
}

/** 计算非全员共有的参数差异列表 */
function computeParamsDiff(okNodes) {
    var hostParams = okNodes.map(function (n) {
        var cache = nodeNginxCache[String(n.id)] || {};
        var set = {};
        (cache.params || []).forEach(function (p) { set[p] = true; });
        return { hostname: n.hostname, set: set };
    });
    var union = {};
    hostParams.forEach(function (hp) {
        Object.keys(hp.set).forEach(function (p) { union[p] = true; });
    });
    var items = [];
    Object.keys(union).sort().forEach(function (param) {
        var hasHosts = [];
        var missHosts = [];
        hostParams.forEach(function (hp) {
            if (hp.set[param]) hasHosts.push(hp.hostname);
            else missHosts.push(hp.hostname);
        });
        if (missHosts.length > 0 && hasHosts.length > 0) {
            items.push({ param: param, hasHosts: hasHosts, missHosts: missHosts });
        }
    });
    return items;
}

/** 切换不一致弹窗中节点完整参数展开 */
function toggleMismatchExpand(rowEl) {
    var nodeId = rowEl.getAttribute('data-node-id');
    var expand = document.querySelector('.mismatch-expand-row[data-expand-for="' + nodeId + '"]');
    if (!expand) return;
    expand.classList.toggle('d-none');
}

/** 打开编译参数编辑区 */
function openConfigEditor(consistent) {
    paramsConsistent = !!consistent;
    var wrap = document.getElementById('refNodeSelectWrap');
    if (paramsConsistent) {
        wrap.classList.add('d-none');
    } else {
        wrap.classList.remove('d-none');
        fillRefNodeSelect();
    }
    syncTargetPrefixVisibility();
    applyRefNodeDisplay();
    document.getElementById('currentConfigBlock').classList.remove('d-none');
    document.getElementById('btnStep3Next').disabled = false;
}

function renderNodeFetchSummary() {
    var box = document.getElementById('nodeFetchSummary');
    var nodes = getSelectedNodeList();
    var html = '<div class="d-flex flex-wrap gap-1">';
    nodes.forEach(function (n) {
        var cache = nodeNginxCache[String(n.id)];
        var cls = 'badge bg-secondary';
        var text = escapeHtml(n.hostname);
        if (cache && cache._error) {
            cls = 'badge bg-danger';
            text += ' 失败';
        } else if (cache) {
            cls = 'badge bg-success';
            text += ' ' + escapeHtml(formatNginxVer(cache.version) || 'ok');
        } else {
            text += ' 待读取';
        }
        html += '<span class="' + cls + '">' + text + '</span>';
    });
    html += '</div>';
    box.innerHTML = html;
}

function fillRefNodeSelect() {
    var sel = document.getElementById('refNodeSelect');
    sel.innerHTML = '';
    getOkNginxNodes().forEach(function (n) {
        var opt = document.createElement('option');
        opt.value = n.id;
        opt.textContent = n.hostname + ' (' + n.ip + ')';
        if (String(n.id) === String(refNodeId)) opt.selected = true;
        sel.appendChild(opt);
    });
}

function onRefNodeChange() {
    refNodeId = document.getElementById('refNodeSelect').value;
    applyRefNodeDisplay();
}

/** 用参考节点填充基线信息，并同步模块增减状态 */
function applyRefNodeDisplay() {
    var info = nodeNginxCache[String(refNodeId)];
    if (!info || info._error) return;

    var ver = formatNginxVer(info.version) || '-';
    var prefix = info.prefix || '-';
    var binary = info.binary_path || '-';
    document.getElementById('currentConfigInfo').innerHTML =
        '<div class="kv-info-box">' +
        '<div class="kv-info-grid kv-info-grid-1">' +
        '<div class="kv-info-row"><span class="kv-info-label">当前版本</span>' +
        '<span class="kv-info-value" title="' + escapeHtml(ver) + '">' + escapeHtml(ver) + '</span></div>' +
        '<div class="kv-info-row"><span class="kv-info-label">安装目录</span>' +
        '<span class="kv-info-value code-font" title="' + escapeHtml(prefix) + '">' + escapeHtml(prefix) + '</span></div>' +
        '<div class="kv-info-row"><span class="kv-info-label">二进制</span>' +
        '<span class="kv-info-value code-font" title="' + escapeHtml(binary) + '">' + escapeHtml(binary) + '</span></div>' +
        '</div></div>';

    syncModuleDeltasWithBaseline(info.params || []);
    updateModuleSummary();
    refreshAnnotatedConfigOutput();
}

/**
 * 切换参考节点后，清理无效的增减勾选
 * @param {string[]} currentParams 基线参数
 */
function syncModuleDeltasWithBaseline(currentParams) {
    var paramSet = {};
    (currentParams || []).forEach(function (p) { paramSet[p] = true; });
    pendingRemovedModules = pendingRemovedModules.filter(function (p) { return !!paramSet[p]; });
    pendingAddedModules = pendingAddedModules.filter(function (p) { return !paramSet[p]; });
}

/** 刷新 Step3 模块调整摘要 */
function updateModuleSummary() {
    var removedEl = document.getElementById('moduleRemovedCount');
    var addedEl = document.getElementById('moduleAddedCount');
    var hintEl = document.getElementById('moduleDeltaHint');
    if (!removedEl) return;
    removedEl.textContent = String(pendingRemovedModules.length);
    addedEl.textContent = String(pendingAddedModules.length);
    if (!pendingRemovedModules.length && !pendingAddedModules.length) {
        hintEl.textContent = '尚未调整';
    } else {
        hintEl.textContent = '已确认增减，将套用到各节点';
    }
}

/** 打开模块调整弹窗 */
function openModuleAdjustModal() {
    var info = nodeNginxCache[String(refNodeId)];
    if (!info || info._error) {
        if (window.showToast) showToast('请先读取 nginx -V', 'warning');
        return;
    }
    draftRemovedModules = pendingRemovedModules.slice();
    draftAddedModules = pendingAddedModules.slice();
    clearModTagInput('removeModTagWrapper', 'removeModSearchField', 'removeModSearchHidden');
    clearModTagInput('addModTagWrapper', 'addModSearchField', 'addModSearchHidden');
    rebuildModuleModalPanels(info.params || []);
    if (!moduleAdjustModal) {
        moduleAdjustModal = new bootstrap.Modal(document.getElementById('moduleAdjustModal'));
    }
    moduleAdjustModal.show();
}

/** 清空模块搜索标签 */
function clearModTagInput(wrapperId, fieldId, hiddenId) {
    var wrapper = document.getElementById(wrapperId);
    var field = document.getElementById(fieldId);
    var hidden = document.getElementById(hiddenId);
    if (!wrapper) return;
    wrapper.querySelectorAll('.tag-badge').forEach(function (b) { b.remove(); });
    if (field) field.value = '';
    if (hidden) hidden.value = '';
}

/**
 * 重建弹窗：左=全部官方 builtin（已编译禁用）；右=当前节点已编译参数
 * @param {string[]} currentParams 基线参数
 */
function rebuildModuleModalPanels(currentParams) {
    modalBaselineParams = (currentParams || []).map(function (p) { return String(p).trim(); }).filter(Boolean);
    var paramSet = {};
    modalBaselineParams.forEach(function (p) { paramSet[p] = true; });

    // 清理草稿中已存在于基线的「新增」项
    draftAddedModules = draftAddedModules.map(function (p) { return String(p).trim(); })
        .filter(function (p) { return p && !isBuiltinModulePresent(paramSet, p); });
    draftRemovedModules = draftRemovedModules.map(function (p) { return String(p).trim(); })
        .filter(function (p) { return paramSet[p]; });

    // 左：全部官方参数；已编译项 disabled + 徽标，保证搜索可命中
    var addGroup = document.getElementById('addModulesGroup');
    addGroup.innerHTML = '';
    var allBuiltin = BUILTIN_ADD_MODULES.map(function (m) { return String(m).trim(); }).filter(Boolean);
    if (!allBuiltin.length) {
        addGroup.innerHTML = '<p class="text-muted small mb-0">无可选编译参数</p>';
    } else {
        allBuiltin.forEach(function (mod) {
            var already = isBuiltinModulePresent(paramSet, mod);
            var label = document.createElement('label');
            label.setAttribute('data-mod-text', mod.toLowerCase());
            if (already) {
                label.className = 'text-muted';
                label.innerHTML = '<input type="checkbox" value="' + escapeHtml(mod) +
                    '" class="add-module" disabled> ' + escapeHtml(mod) +
                    ' <span class="badge bg-secondary ms-1">已编译</span>';
            } else {
                var checked = draftAddedModules.indexOf(mod) >= 0 ? ' checked' : '';
                label.innerHTML = '<input type="checkbox" value="' + escapeHtml(mod) +
                    '" class="add-module"' + checked + '> ' + escapeHtml(mod);
                label.querySelector('input').addEventListener('change', function () {
                    onModuleModalDraftChange();
                });
            }
            addGroup.appendChild(label);
        });
    }

    // 右：已编译参数（勾选移除）
    var removeGroup = document.getElementById('removeModulesGroup');
    removeGroup.innerHTML = '';
    if (!modalBaselineParams.length) {
        removeGroup.innerHTML = '<p class="text-muted small mb-0">无已编译参数</p>';
    } else {
        modalBaselineParams.forEach(function (param) {
            var label = document.createElement('label');
            label.setAttribute('data-mod-text', param.toLowerCase());
            var checked = draftRemovedModules.indexOf(param) >= 0 ? ' checked' : '';
            label.innerHTML = '<input type="checkbox" value="' + escapeHtml(param) +
                '" class="remove-module"' + checked + '> ' + escapeHtml(param);
            label.querySelector('input').addEventListener('change', function () {
                onModuleModalDraftChange();
            });
            removeGroup.appendChild(label);
        });
    }

    filterModuleModalLists();
}

/** 从弹窗 checkbox 同步草稿（跳过 disabled 的已编译项） */
function syncDraftFromModal() {
    draftRemovedModules = [];
    document.querySelectorAll('#removeModulesGroup .remove-module:checked').forEach(function (cb) {
        draftRemovedModules.push(String(cb.value).trim());
    });
    draftAddedModules = [];
    document.querySelectorAll('#addModulesGroup .add-module:checked:not(:disabled)').forEach(function (cb) {
        var v = String(cb.value).trim();
        if (modalBaselineParams.indexOf(v) < 0) draftAddedModules.push(v);
    });
}

/** 草稿勾选变化 */
function onModuleModalDraftChange() {
    syncDraftFromModal();
    filterModuleModalLists();
}

/** 确认模块弹窗勾选写入内存状态 */
function confirmModuleAdjust() {
    syncDraftFromModal();
    pendingRemovedModules = draftRemovedModules.slice();
    pendingAddedModules = draftAddedModules.slice();
    updateModuleSummary();
    refreshAnnotatedConfigOutput();
    if (moduleAdjustModal) moduleAdjustModal.hide();
    saveUpgradeWizardState();
}

/** 统计分组内可见的模块 label 数量 */
function countVisibleModuleLabels(groupId) {
    var group = document.getElementById(groupId);
    if (!group) return 0;
    var n = 0;
    group.querySelectorAll('label[data-mod-text]').forEach(function (label) {
        if (label.style.display !== 'none') n += 1;
    });
    return n;
}

/** 按给定搜索词统计分组内匹配的 label 数（不改变 display） */
function countModuleLabelsMatching(groupId, tags) {
    var group = document.getElementById(groupId);
    if (!group) return 0;
    var n = 0;
    group.querySelectorAll('label[data-mod-text]').forEach(function (label) {
        var text = label.getAttribute('data-mod-text') || '';
        var ok = !tags.length || tags.every(function (q) { return text.indexOf(q) >= 0; });
        if (ok) n += 1;
    });
    return n;
}

/** 更新或清除分组顶部的交叉搜索提示 */
function setModuleGroupSearchHint(groupId, text) {
    var group = document.getElementById(groupId);
    if (!group) return;
    var old = group.querySelector('.module-search-cross-hint');
    if (old) old.remove();
    if (!text) return;
    var tip = document.createElement('p');
    tip.className = 'text-muted small mb-2 module-search-cross-hint';
    tip.textContent = text;
    group.insertBefore(tip, group.firstChild);
}

/** 按标签过滤弹窗左右列表，并给出交叉空结果提示 */
function filterModuleModalLists() {
    filterOneModuleGroup('addModSearchHidden', 'addModSearchField', 'addModulesGroup');
    filterOneModuleGroup('removeModSearchHidden', 'removeModSearchField', 'removeModulesGroup');

    var addQuery = getModuleGroupSearchQuery('addModSearchHidden', 'addModSearchField');
    var removeQuery = getModuleGroupSearchQuery('removeModSearchHidden', 'removeModSearchField');
    var addVisible = countVisibleModuleLabels('addModulesGroup');
    var removeVisible = countVisibleModuleLabels('removeModulesGroup');

    // 左侧无匹配，但用同一关键词在右侧能命中 → 提示去右侧看已编译
    if (addQuery.length && addVisible === 0 &&
        countModuleLabelsMatching('removeModulesGroup', addQuery) > 0) {
        setModuleGroupSearchHint(
            'addModulesGroup',
            '无新增项可勾选；已在右侧「已编译参数」中'
        );
    } else {
        setModuleGroupSearchHint('addModulesGroup', '');
    }

    // 右侧无匹配，但用同一关键词在左侧能命中 → 提示去左侧添加
    if (removeQuery.length && removeVisible === 0 &&
        countModuleLabelsMatching('addModulesGroup', removeQuery) > 0) {
        setModuleGroupSearchHint(
            'removeModulesGroup',
            '当前未编译该参数；可到左侧「可选编译参数」勾选添加'
        );
    } else {
        setModuleGroupSearchHint('removeModulesGroup', '');
    }
}

/** 读取某一侧当前搜索关键词（含标签与输入框） */
function getModuleGroupSearchQuery(hiddenId, fieldId) {
    var hidden = document.getElementById(hiddenId);
    var field = document.getElementById(fieldId);
    var tags = (hidden && hidden.value ? hidden.value.split(',') : [])
        .map(function (t) { return t.trim().toLowerCase(); })
        .filter(Boolean);
    var live = field && field.value.trim().toLowerCase();
    if (live && tags.indexOf(live) < 0) tags = tags.concat([live]);
    return tags;
}

/** 按搜索词过滤单个模块分组 */
function filterOneModuleGroup(hiddenId, fieldId, groupId) {
    var tags = getModuleGroupSearchQuery(hiddenId, fieldId);
    var group = document.getElementById(groupId);
    if (!group) return;
    group.querySelectorAll('label[data-mod-text]').forEach(function (label) {
        var text = label.getAttribute('data-mod-text') || '';
        var ok = !tags.length || tags.every(function (q) { return text.indexOf(q) >= 0; });
        label.style.display = ok ? '' : 'none';
    });
}

/** 初始化模块弹窗两侧 tag-input */
function initModuleAdjustTagInputs() {
    setupModTagInput('removeModTagWrapper', 'removeModSearchField', 'removeModSearchHidden', filterModuleModalLists);
    setupModTagInput('addModTagWrapper', 'addModSearchField', 'addModSearchHidden', filterModuleModalLists);
}

function setupModTagInput(wrapperId, fieldId, hiddenId, onChange) {
    var wrapper = document.getElementById(wrapperId);
    var field = document.getElementById(fieldId);
    var hidden = document.getElementById(hiddenId);
    if (!wrapper || !field || !hidden) return;

    function updateHidden() {
        var values = [];
        wrapper.querySelectorAll('.tag-badge').forEach(function (b) {
            values.push(b.getAttribute('data-value'));
        });
        hidden.value = values.join(',');
    }

    function createBadge(text) {
        if (!text) return;
        if (wrapper.querySelector('.tag-badge[data-value="' + text + '"]')) return;
        var badge = document.createElement('span');
        badge.className = 'tag-badge';
        badge.setAttribute('data-value', text);
        badge.innerHTML = text + '<span class="tag-remove">&times;</span>';
        badge.querySelector('.tag-remove').addEventListener('click', function (e) {
            e.stopPropagation();
            badge.remove();
            updateHidden();
            onChange();
        });
        wrapper.insertBefore(badge, field);
        updateHidden();
    }

    field.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            var val = field.value.trim();
            if (val) {
                createBadge(val);
                field.value = '';
                onChange();
            }
        } else if (e.key === 'Backspace' && !field.value) {
            var badges = wrapper.querySelectorAll('.tag-badge');
            if (badges.length) {
                badges[badges.length - 1].remove();
                updateHidden();
                onChange();
            }
        }
    });

    var searchTimer = null;
    field.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
            var live = field.value.trim();
            var base = [];
            wrapper.querySelectorAll('.tag-badge').forEach(function (b) {
                base.push(b.getAttribute('data-value'));
            });
            if (live) base.push(live);
            hidden.value = base.join(',');
            onChange();
        }, 200);
    });

    wrapper.addEventListener('click', function (e) {
        if (e.target === wrapper) field.focus();
    });
}

// ========== 预览 Step4 ==========
/** 进入确认清单步骤
 * @param {boolean} [keepConfirmCheck] 恢复状态时保留确认勾选
 */
function previewAndGoStep4(keepConfirmCheck) {
    var nodes = getSelectedNodeList();
    var pkg = getSelectedPackage();
    if (!nodes.length || !pkg) {
        if (window.showToast) showToast('请先完成目标选择', 'warning');
        return Promise.resolve();
    }
    if (getUpgradeMode() === 'switch_path') {
        var tp = (document.getElementById('targetPrefix').value || '').trim();
        if (!tp) {
            if (window.showToast) showToast('切换路径模式请填写目标安装目录', 'warning');
            return Promise.resolve();
        }
    }
    var deltas = getModuleDeltas();
    var okNodes = getOkNginxNodes();
    if (!okNodes.length) {
        if (window.showToast) showToast('没有可用的 nginx -V 基线', 'warning');
        return Promise.resolve();
    }

    var btn = document.getElementById('btnStep3Next');
    btn.disabled = true;

    var promises = okNodes.map(function (node) {
        var cache = nodeNginxCache[String(node.id)];
        var formData = new FormData();
        formData.append('current_params', JSON.stringify(cache.params || []));
        formData.append('added_modules', JSON.stringify(deltas.addedModules));
        formData.append('removed_modules', JSON.stringify(deltas.removedModules));
        formData.append('added_third_party', JSON.stringify(deltas.thirdParty));
        return fetch('{% url "upgrade:api_compute_config" %}', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken(), 'X-Requested-With': 'XMLHttpRequest' },
            body: formData
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.success) {
                nodeTargetOptsCache[String(node.id)] = applyPrefixToTargetOpts(
                    data.target_opts, cache.prefix || ''
                );
            } else {
                nodeTargetOptsCache[String(node.id)] = '';
            }
        });
    });

    return Promise.all(promises).then(function () {
        btn.disabled = false;
        buildConfirmView(okNodes, pkg, deltas);
        showWizardStep(4);
        if (!keepConfirmCheck) {
            document.getElementById('confirmCheck').checked = false;
            document.getElementById('btnStartUpgrade').disabled = true;
        }
    });
}

/**
 * 渲染确认摘要中的模块列表：默认首项，多项可点「详情」展开
 * @param {string} label 标签文案
 * @param {string[]} items 参数列表
 * @param {string} detailId 详情容器 id
 */
function renderSummaryModuleLine(label, items, detailId) {
    var list = (items || []).map(function (x) { return String(x).trim(); }).filter(Boolean);
    if (!list.length) {
        return '<div class="kv-info-mod"><div class="kv-info-row">' +
            '<span class="kv-info-label">' + escapeHtml(label) + '</span>' +
            '<span class="kv-info-value">无</span></div></div>';
    }
    if (list.length === 1) {
        return '<div class="kv-info-mod"><div class="kv-info-row">' +
            '<span class="kv-info-label">' + escapeHtml(label) + '</span>' +
            '<span class="kv-mod-first" title="' + escapeHtml(list[0]) + '">' +
            escapeHtml(list[0]) + '</span></div></div>';
    }
    var detailHtml = list.map(function (p) {
        return '<div class="kv-mod-item">' + escapeHtml(p) + '</div>';
    }).join('');
    return '<div class="kv-info-mod">' +
        '<div class="kv-info-row">' +
        '<span class="kv-info-label">' + escapeHtml(label) + '</span>' +
        '<span class="kv-mod-first" title="' + escapeHtml(list[0]) + '">' + escapeHtml(list[0]) + '</span>' +
        '<button type="button" class="kv-mod-toggle" data-detail-id="' + escapeHtml(detailId) + '"' +
        ' onclick="toggleSummaryModDetail(this)" aria-expanded="false">详情</button></div>' +
        '<div id="' + escapeHtml(detailId) + '" class="kv-mod-detail d-none">' + detailHtml + '</div>' +
        '</div>';
}

/** 切换确认摘要模块详情展开/收起 */
function toggleSummaryModDetail(btn) {
    var id = btn.getAttribute('data-detail-id');
    var el = document.getElementById(id);
    if (!el) return;
    var open = el.classList.contains('d-none');
    el.classList.toggle('d-none', !open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.textContent = open ? '收起' : '详情';
}

/** 渲染摘要一行 label/value（冒号由 CSS ::after 追加） */
function renderSummaryInfoRow(label, value) {
    var text = value == null || value === '' ? '-' : String(value);
    return '<div class="kv-info-row">' +
        '<span class="kv-info-label">' + escapeHtml(label) + '</span>' +
        '<span class="kv-info-value" title="' + escapeHtml(text) + '">' + escapeHtml(text) + '</span>' +
        '</div>';
}

function buildConfirmView(okNodes, pkg, deltas) {
    var modeMap = { upgrade: '平滑升级', install: '全新安装', switch_path: '切换路径' };
    var mode = getUpgradeMode();
    var prefix = (document.getElementById('targetPrefix').value || '').trim();
    var refCache = nodeNginxCache[String(refNodeId)] || nodeNginxCache[String(okNodes[0].id)];
    var refOpts = nodeTargetOptsCache[String(refNodeId)] || nodeTargetOptsCache[String(okNodes[0].id)] || '';
    var prefixLabel = mode === 'switch_path' ? (prefix || '-') : '各节点自身';
    var pkgLabel = (pkg.name || '') + ' (' + formatNginxVer(pkg.version) + ')';

    document.getElementById('upgradeSummary').innerHTML =
        '<div class="kv-info-grid">' +
        renderSummaryInfoRow('目标节点', okNodes.length + ' 台') +
        renderSummaryInfoRow('源码包', pkgLabel) +
        renderSummaryInfoRow('升级模式', modeMap[mode] || mode) +
        renderSummaryInfoRow('工作目录', document.getElementById('remoteWorkDir').value) +
        renderSummaryInfoRow('并行编译', '-j' + document.getElementById('makeJobs').value) +
        renderSummaryInfoRow('目标安装目录', prefixLabel) +
        '</div>' +
        renderSummaryModuleLine('新增模块', deltas.addedModules, 'summaryAddedDetail') +
        renderSummaryModuleLine('移除参数', deltas.removedModules, 'summaryRemovedDetail');

    var tokens = tokenizeOpts(refOpts);
    document.getElementById('finalConfigPreview').innerHTML =
        renderFinalParamsWithAnnotations(refCache.params || [], tokens, deltas);

    var listHtml = '';
    okNodes.forEach(function (n) {
        var cache = nodeNginxCache[String(n.id)] || {};
        var groups = (n.group_names || []).map(function (g) {
            return '<span class="badge bg-info text-dark me-1">' + escapeHtml(g) + '</span>';
        }).join('');
        var nodePrefix = mode === 'switch_path' ? (prefix || cache.prefix || '') : (cache.prefix || '');
        listHtml +=
            '<div class="confirm-node-block" data-node-id="' + n.id + '">' +
            '<div class="confirm-node-header">' +
            '<span class="node-info-cell"><span class="node-identity">' + escapeHtml(n.hostname) +
            '<small class="text-muted">(' + escapeHtml(n.ip) + ')</small></span> ' + groups + '</span>' +
            '<span class="small text-muted text-end">' +
            escapeHtml(formatNginxVer(cache.version)) + ' → ' + escapeHtml(formatNginxVer(pkg.version)) +
            (nodePrefix ? '<br><span class="code-font">安装目录: ' + escapeHtml(nodePrefix) + '</span>' : '') +
            '</span></div></div>';
    });
    document.getElementById('confirmNodeList').innerHTML = listHtml;
}

// ========== 开始升级 ==========
/** 发起升级：进度区已有任务时合并警告为单次确认（避免嵌套 showConfirm 丢回调） */
function startUpgrade() {
    var n = getSelectedNodeList().length;
    var title = '确认升级';
    var msg = '确认对 ' + n + ' 台节点开始 Nginx 编译升级？升级将在后台并行执行。';
    if (currentTaskIds.length > 0) {
        title = '再次开始升级';
        msg = '当前升级执行进度中已有任务，再次开始将创建新的升级批次。\n\n'
            + '确认对 ' + n + ' 台节点开始 Nginx 编译升级？升级将在后台并行执行。';
    }
    showConfirm(title, msg, function () {
        doStartUpgrade();
    });
}

function doStartUpgrade() {
    var nodes = getOkNginxNodes();
    var pkg = getSelectedPackage();
    if (!nodes.length || !pkg) {
        if (window.showToast) showToast('请先完成配置', 'warning');
        return;
    }
    if (getUpgradeMode() === 'switch_path') {
        var tp = (document.getElementById('targetPrefix').value || '').trim();
        if (!tp) {
            if (window.showToast) showToast('切换路径模式请填写目标安装目录', 'warning');
            return;
        }
    }
    var deltas = getModuleDeltas();
    var nodesPayload = nodes.map(function (n) {
        var c = nodeNginxCache[String(n.id)];
        return {
            node_id: n.id,
            current_version: c.version || '',
            current_configure_opts: c.configure_opts || '',
            params: c.params || [],
            prefix: c.prefix || '',
            binary_path: c.binary_path || '',
        };
    });

    var payload = {
        node_ids: nodes.map(function (n) { return n.id; }),
        source_package: parseInt(pkg.id, 10),
        upgrade_mode: getUpgradeMode(),
        remote_work_dir: document.getElementById('remoteWorkDir').value,
        make_jobs: parseInt(document.getElementById('makeJobs').value, 10) || UPGRADE_SETTINGS_MAKE_JOBS,
        target_version: pkg.version,
        target_prefix: getUpgradeMode() === 'switch_path'
            ? ((document.getElementById('targetPrefix').value || '').trim())
            : '',
        added_modules: deltas.addedModules,
        removed_modules: deltas.removedModules,
        added_third_party: deltas.thirdParty,
        nodes_payload: nodesPayload,
    };

    fetch('{% url "upgrade:task_create" %}', {
        method: 'POST',
        headers: {
            'X-CSRFToken': getCSRFToken(),
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        body: JSON.stringify(payload),
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
        if (!data.success) {
            if (window.showToast) showToast('创建失败: ' + (data.message || ''), 'danger');
            return;
        }
        currentTaskIds = data.task_ids || (data.task_id ? [data.task_id] : []);
        currentBatchNumber = data.batch_number || '';
        showProgressLive();
        document.getElementById('batchNumberLabel').textContent =
            currentBatchNumber ? ('批次: ' + currentBatchNumber) : '';
        startBatchPolling();
        document.getElementById('btnCancel').classList.remove('d-none');
        // 勾选仍有效时保持「开始升级」可点，便于再次开跑（会先警告）
        document.getElementById('btnStartUpgrade').disabled =
            !document.getElementById('confirmCheck').checked;
        saveUpgradeWizardState();
        if (window.showToast) showToast(data.message || '升级任务已创建', 'success');
    })
    .catch(function (err) {
        if (window.showToast) showToast('请求失败: ' + err.message, 'danger');
    });
}

function showProgressLive() {
    document.getElementById('progressPlaceholder').classList.add('d-none');
    document.getElementById('progressLive').classList.remove('d-none');
    document.getElementById('progressPercent').classList.remove('d-none', 'bg-secondary', 'bg-success', 'bg-danger');
    document.getElementById('progressPercent').classList.add('bg-primary');
    var bar = document.getElementById('progressBar');
    bar.classList.add('progress-bar-animated', 'progress-bar-striped');
    bar.classList.remove('bg-success', 'bg-danger');
    bar.style.width = '0%';
}

function startBatchPolling() {
    if (pollTimer) clearInterval(pollTimer);
    var ids = currentTaskIds.join(',');
    var url = '{% url "upgrade:api_batch_progress" %}?ids=' + encodeURIComponent(ids);

    function pollOnce() {
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success) return;
            document.getElementById('progressPercent').textContent = data.progress + '%';
            document.getElementById('progressBar').style.width = data.progress + '%';
            renderBatchProgress(data.tasks || []);

            if (data.all_done) {
                clearInterval(pollTimer);
                pollTimer = null;
                document.getElementById('btnCancel').classList.add('d-none');
                var bar = document.getElementById('progressBar');
                bar.classList.remove('progress-bar-animated', 'progress-bar-striped');
                var pct = document.getElementById('progressPercent');
                pct.classList.remove('bg-primary');
                if (data.all_success) {
                    bar.classList.add('bg-success');
                    pct.classList.add('bg-success');
                } else {
                    bar.classList.add('bg-danger');
                    pct.classList.add('bg-danger');
                }
            }
        });
    }
    pollOnce();
    pollTimer = setInterval(pollOnce, {{ sys_poll_interval_ms|default:2000 }});
}

/** 截断错误信息为前 N 行，完整内容见任务详情日志 */
function truncateErrorLines(msg, maxLines) {
    var text = String(msg || '').replace(/\r\n/g, '\n').trim();
    if (!text) return '未知错误';
    var lines = text.split('\n');
    var limit = maxLines || 5;
    if (lines.length <= limit) return text;
    return lines.slice(0, limit).join('\n') + '\n…';
}

function renderBatchProgress(tasks) {
    var container = document.getElementById('upgradeSteps');
    var html = '';
    tasks.forEach(function (t, idx) {
        var open = idx === 0 ? '' : ' d-none';
        var badgeCls = 'bg-secondary';
        if (t.status === 'success') badgeCls = 'bg-success';
        else if (t.status === 'failed' || t.status === 'cancelled') badgeCls = 'bg-danger';
        else if (t.status !== 'pending') badgeCls = 'bg-primary';
        html +=
            '<div class="node-progress-block" data-task-id="' + t.task_id + '">' +
            '<div class="node-progress-header" onclick="toggleNodeProgress(this)">' +
            '<span>' + escapeHtml(t.hostname) +
            ' <small class="text-muted">(' + escapeHtml(t.ip) + ')</small></span>' +
            '<span><span class="badge ' + badgeCls + '">' + escapeHtml(t.status_display || t.status) +
            '</span> ' + t.progress + '% <i class="bi bi-chevron-down"></i></span></div>' +
            '<div class="node-progress-body' + open + '">' +
            buildStepHtml(t) +
            (t.log_url ? '<a class="small" href="' + t.log_url + '" target="_blank">查看完整日志</a>' : '') +
            '</div></div>';
    });
    container.innerHTML = html || '<div class="text-muted small">等待进度...</div>';
}

function toggleNodeProgress(headerEl) {
    var body = headerEl.parentElement.querySelector('.node-progress-body');
    if (body) body.classList.toggle('d-none');
}

function buildStepHtml(data) {
    var steps = [
        { key: 'fetching_config', label: '获取当前编译参数', step: 1 },
        { key: 'uploading_package', label: '上传源码包到节点', step: 2 },
        { key: 'downloading_modules', label: '下载第三方模块', step: 3 },
        { key: 'configuring', label: '执行 ./configure', step: 4 },
        { key: 'compiling', label: '执行 make -j', step: 5 },
        { key: 'backing_up', label: '备份旧版本', step: 6 },
        { key: 'replacing_binary', label: '安装新版本 (make install)', step: 7 },
        { key: 'upgrading', label: '按启动方式 reload Nginx', step: 8 },
        { key: 'verifying', label: '验证版本 & 运行状态', step: 9 },
    ];
    var html = '';
    var isFailed = data.status === 'failed';
    var isSuccess = data.status === 'success';
    var isCancelled = data.status === 'cancelled';

    steps.forEach(function (s) {
        var cls = 'pending', icon = '<i class="bi bi-hourglass-split"></i>';
        var stepProgress = s.step * 11;
        if (isSuccess || stepProgress <= data.progress) {
            cls = 'success'; icon = '<i class="bi bi-check-circle-fill"></i>';
        } else if (s.key === data.status) {
            cls = 'running'; icon = '<i class="bi bi-arrow-repeat"></i>';
        }
        if (isFailed && data.progress < stepProgress) {
            cls = 'pending'; icon = '<i class="bi bi-hourglass-split"></i>';
        }
        if (isFailed && s.key === data.status) {
            cls = 'failed'; icon = '<i class="bi bi-x-circle-fill"></i>';
        }
        if (isCancelled && data.progress < stepProgress) {
            cls = 'pending'; icon = '<i class="bi bi-hourglass-split"></i>';
        }
        html += '<div class="upgrade-step ' + cls + '">' + icon + ' ' + s.step + '. ' + s.label + '</div>';
    });
    if (isSuccess) {
        html += '<div class="upgrade-step success"><i class="bi bi-check-circle-fill"></i> 升级成功</div>';
    }
    if (isFailed) {
        var brief = truncateErrorLines(data.error_message, 5);
        html += '<div class="upgrade-step failed" style="align-items:flex-start;white-space:pre-wrap;">' +
            '<i class="bi bi-x-circle-fill me-1"></i><span>失败:\n' +
            escapeHtml(brief) + '</span></div>';
    }
    return html;
}

function cancelUpgrade() {
    if (!currentTaskIds.length) return;
    showConfirm('确认取消', '确定取消本批次尚未完成的升级任务？', function () {
        var pending = currentTaskIds.map(function (id) {
            return fetch(`{% url 'upgrade:task_cancel' 0 %}`.replace('0', id), {
                method: 'POST',
                headers: { 'X-CSRFToken': getCSRFToken(), 'X-Requested-With': 'XMLHttpRequest' }
            }).then(function (r) { return r.json(); });
        });
        Promise.all(pending).then(function () {
            document.getElementById('btnCancel').classList.add('d-none');
        });
    });
}

function addTpModule() {
    var container = document.getElementById('thirdPartyModules');
    var row = document.createElement('div');
    row.className = 'row mb-2 tp-module-row';
    row.innerHTML =
        '<div class="col-3"><input type="text" class="form-control form-control-sm tp-name" placeholder="模块名"></div>' +
        '<div class="col-6"><input type="text" class="form-control form-control-sm tp-git" placeholder="Git 仓库 URL"></div>' +
        '<div class="col-2"><input type="text" class="form-control form-control-sm tp-branch" placeholder="分支"></div>' +
        '<div class="col-1"><button type="button" class="btn btn-sm btn-outline-danger" onclick="removeTpModule(this)">×</button></div>';
    container.appendChild(row);
    saveUpgradeWizardState();
}

function removeTpModule(btn) {
    btn.closest('.tp-module-row').remove();
    saveUpgradeWizardState();
}

// ========== 向导状态持久化（刷新保持当前步骤） ==========
var UPGRADE_WIZARD_STORAGE_KEY = 'mngxops_upgrade_center_v1';
var _skipWizardSave = false;
/** 当前页服务端注入的系统设置默认值（所见即所得） */
var UPGRADE_SETTINGS_WORK_DIR = "{{ default_work_dir|default:'/tmp/nginx-upgrade'|escapejs }}";
var UPGRADE_SETTINGS_MAKE_JOBS = {{ default_make_jobs|default:4 }};

/** 收集第三方模块行数据 */
function collectThirdPartyRows() {
    var rows = [];
    document.querySelectorAll('.tp-module-row').forEach(function (row) {
        rows.push({
            name: (row.querySelector('.tp-name') || {}).value || '',
            git: (row.querySelector('.tp-git') || {}).value || '',
            branch: (row.querySelector('.tp-branch') || {}).value || '',
        });
    });
    return rows;
}

/** 用数据重建第三方模块行 */
function applyThirdPartyRows(rows) {
    var container = document.getElementById('thirdPartyModules');
    if (!container) return;
    container.innerHTML = '';
    var list = (rows && rows.length) ? rows : [{ name: '', git: '', branch: '' }];
    list.forEach(function (item) {
        var row = document.createElement('div');
        row.className = 'row mb-2 tp-module-row';
        row.innerHTML =
            '<div class="col-3"><input type="text" class="form-control form-control-sm tp-name" placeholder="模块名"></div>' +
            '<div class="col-6"><input type="text" class="form-control form-control-sm tp-git" placeholder="Git 仓库 URL"></div>' +
            '<div class="col-2"><input type="text" class="form-control form-control-sm tp-branch" placeholder="分支"></div>' +
            '<div class="col-1"><button type="button" class="btn btn-sm btn-outline-danger" onclick="removeTpModule(this)">×</button></div>';
        row.querySelector('.tp-name').value = item.name || '';
        row.querySelector('.tp-git').value = item.git || '';
        row.querySelector('.tp-branch').value = item.branch || '';
        container.appendChild(row);
    });
}

/** 保存升级中心状态到 sessionStorage（仅进行中批次，不持久化向导草稿） */
function saveUpgradeWizardState() {
    if (_skipWizardSave) return;
    try {
        // 节点/包/参数等草稿不跨页保留；仅续看执行中批次进度（Q113）
        var state = {
            currentTaskIds: currentTaskIds,
            currentBatchNumber: currentBatchNumber,
        };
        if (currentTaskIds && currentTaskIds.length) {
            sessionStorage.setItem(UPGRADE_WIZARD_STORAGE_KEY, JSON.stringify(state));
        } else {
            sessionStorage.removeItem(UPGRADE_WIZARD_STORAGE_KEY);
        }
    } catch (e) { /* ignore quota */ }
}

/** 从 sessionStorage 恢复：仅进行中批次进度；向导始终从第 1 步空白开始 */
function restoreUpgradeWizardState() {
    var raw;
    try {
        raw = sessionStorage.getItem(UPGRADE_WIZARD_STORAGE_KEY);
    } catch (e) {
        showWizardStep(1);
        return Promise.resolve();
    }
    var state = null;
    if (raw) {
        try {
            state = JSON.parse(raw);
        } catch (e) {
            state = null;
        }
    }

    _skipWizardSave = true;
    currentTaskIds = (state && state.currentTaskIds) || [];
    currentBatchNumber = (state && state.currentBatchNumber) || '';

    // 进行中批次：续看进度（与向导草稿无关）
    if (currentTaskIds.length) {
        showProgressLive();
        document.getElementById('batchNumberLabel').textContent =
            currentBatchNumber ? ('批次: ' + currentBatchNumber) : '';
        startBatchPolling();
        document.getElementById('btnCancel').classList.remove('d-none');
    }

    // 再进入：第 1 步且不恢复节点/包/参数草稿
    showWizardStep(1);
    _skipWizardSave = false;
    saveUpgradeWizardState();
    return Promise.resolve();
}

document.addEventListener('DOMContentLoaded', function () {
    initPackageTable();
    initPackageTagInput();
    initUpgradeNodeTagInput();
    initModuleAdjustTagInputs();
    upgradeSelectNodeModal = new bootstrap.Modal(document.getElementById('upgradeSelectNodeModal'));
    paramsMismatchModal = new bootstrap.Modal(document.getElementById('paramsMismatchModal'));
    moduleAdjustModal = new bootstrap.Modal(document.getElementById('moduleAdjustModal'));

    document.getElementById('upgradeSelectNodeModal').addEventListener('shown.bs.modal', function () {
        var field = document.getElementById('upgradeNodeSearchField');
        var wrapper = document.getElementById('upgradeNodeTagWrapper');
        var hidden = document.getElementById('upgradeNodeSearchHidden');
        field.value = '';
        wrapper.querySelectorAll('.tag-badge').forEach(function (b) { b.remove(); });
        hidden.value = '';
        modalPickedNodes = {};
        Object.keys(selectedNodes).forEach(function (k) {
            modalPickedNodes[k] = Object.assign({}, selectedNodes[k]);
        });
        updateModalPickedCount();
        field.focus();
        doUpgradeNodeSearch();
    });

    document.getElementById('modalSelectAllNodes').addEventListener('change', function () {
        var checked = this.checked;
        document.querySelectorAll('#upgradeSearchResultTbody .upgrade-node-cb:not(:disabled)').forEach(function (cb) {
            cb.checked = checked;
            var nodeId = cb.value;
            var node = lastSearchNodes.find(function (n) { return String(n.id) === String(nodeId); });
            toggleModalPick(node, checked);
        });
        updateModalPickedCount();
    });

    document.getElementById('confirmCheck').addEventListener('change', function () {
        document.getElementById('btnStartUpgrade').disabled = !this.checked;
        saveUpgradeWizardState();
    });

    document.querySelectorAll('input[name="upgradeMode"]').forEach(function (el) {
        el.addEventListener('change', function () {
            syncTargetPrefixVisibility();
            saveUpgradeWizardState();
        });
    });
    syncTargetPrefixVisibility();

    document.getElementById('paramsMismatchBackBtn').addEventListener('click', function () {
        if (paramsMismatchModal) paramsMismatchModal.hide();
        document.getElementById('currentConfigBlock').classList.add('d-none');
        document.getElementById('btnStep3Next').disabled = true;
        goStep(1);
    });
    document.getElementById('paramsMismatchContinueBtn').addEventListener('click', function () {
        if (paramsMismatchModal) paramsMismatchModal.hide();
        openConfigEditor(false);
    });

    document.getElementById('confirmModuleAdjustBtn').addEventListener('click', confirmModuleAdjust);
    updateModuleSummary();

    document.querySelectorAll('.pkg-radio').forEach(function (r) {
        r.addEventListener('change', function () { saveUpgradeWizardState(); });
    });
    ['remoteWorkDir', 'makeJobs', 'targetPrefix'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', saveUpgradeWizardState);
    });

    restoreUpgradeWizardState();
});
