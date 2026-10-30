(function (window, document) {
    "use strict";

    var requestVersion = 0;
    var savedViews = {};

    /** 判断地址是否属于升级模块页面。 */
    function isUpgradePage(url) {
        return url.origin === window.location.origin && /^\/upgrade(?:\/|$)/.test(url.pathname);
    }

    /** 生成页面视图的缓存键。 */
    function viewKey(url) {
        return url.pathname + url.search;
    }

    /** 保存升级中心的步骤一选择，便于包管理页面返回时恢复。 */
    function captureViewState(url) {
        var center = document.getElementById("upgradeStepper");
        if (!center) return;
        savedViews[viewKey(url)] = {
            nodeIds: Array.from(document.querySelectorAll(".upgrade-node-check:checked")).map(function (input) {
                return input.value;
            }),
            packageId: (document.querySelector(".upgrade-package-check:checked") || {}).value || ""
        };
    }

    /** 将缓存的步骤一选择恢复到新页面文档。 */
    function restoreViewState(pageDocument, url) {
        var state = savedViews[viewKey(url)];
        if (!state || !pageDocument.getElementById("upgradeStepper")) return;
        state.nodeIds.forEach(function (id) {
            var input = pageDocument.querySelector(".upgrade-node-check[value='" + id + "']");
            if (input) input.checked = true;
        });
        if (state.packageId) {
            var packageInput = pageDocument.querySelector(
                ".upgrade-package-check[value='" + state.packageId + "']"
            );
            if (packageInput) packageInput.checked = true;
        }
    }

    /** 判断样式表是否为页面专属资源。 */
    function isPageStylesheet(link) {
        var href = link.getAttribute("href") || "";
        return href.indexOf("/static/css/") >= 0 && !/\/static\/css\/app\.css(?:\?|$)/.test(href);
    }

    /** 更新页面内容区和升级页专属的弹窗、标题及样式。 */
    function replacePageView(pageDocument) {
        var nextContent = pageDocument.getElementById("pageContent");
        var currentContent = document.getElementById("pageContent");
        if (!nextContent || !currentContent) return false;
        currentContent.replaceWith(document.importNode(nextContent, true));

        var nextModals = pageDocument.getElementById("pageModals");
        var currentModals = document.getElementById("pageModals");
        if (nextModals && currentModals) {
            currentModals.replaceWith(document.importNode(nextModals, true));
        }

        var nextHeading = pageDocument.querySelector(".page-heading");
        var currentHeading = document.querySelector(".page-heading");
        if (nextHeading && currentHeading) currentHeading.innerHTML = nextHeading.innerHTML;
        document.title = pageDocument.title;

        Array.from(document.head.querySelectorAll("link[rel='stylesheet'][href]"))
            .filter(isPageStylesheet)
            .forEach(function (link) { link.remove(); });
        Array.from(pageDocument.head.querySelectorAll("link[rel='stylesheet'][href]"))
            .filter(isPageStylesheet)
            .forEach(function (link) {
                document.head.appendChild(document.importNode(link, true));
            });

        var nextToken = pageDocument.querySelector("meta[name='csrf-token']");
        var currentToken = document.querySelector("meta[name='csrf-token']");
        if (nextToken && currentToken) currentToken.content = nextToken.content;
        return true;
    }

    /** 依次执行目标页面脚本，并在脚本失败时回退到整页导航。 */
    function executePageScripts(pageDocument, fallbackUrl, done) {
        var host = document.getElementById("pageScripts");
        var scripts = Array.from(pageDocument.querySelectorAll("#pageScripts script"));
        if (!host) {
            done();
            return;
        }
        host.replaceChildren();

        function appendAt(index) {
            if (index >= scripts.length) {
                done();
                return;
            }
            var source = scripts[index];
            var script = document.createElement("script");
            Array.from(source.attributes).forEach(function (attribute) {
                script.setAttribute(attribute.name, attribute.value);
            });
            if (source.src) {
                script.async = false;
                script.onload = function () { appendAt(index + 1); };
                script.onerror = function () { window.location.assign(fallbackUrl); };
                host.appendChild(script);
                return;
            }
            script.textContent = source.textContent;
            host.appendChild(script);
            appendAt(index + 1);
        }

        appendAt(0);
    }

    /** 请求并渲染升级模块页面，保留全局布局和侧栏。 */
    function navigate(url, method, body, pushHistory) {
        var destination = new URL(url, window.location.href);
        if (!isUpgradePage(destination)) {
            window.location.assign(destination.href);
            return;
        }
        var version = ++requestVersion;
        var headers = { "X-Requested-With": "XMLHttpRequest", "Accept": "text/html" };
        var options = { method: method || "GET", credentials: "same-origin", headers: headers };
        if (body) options.body = body;

        fetch(destination.href, options).then(function (response) {
            var responseUrl = new URL(response.url || destination.href, window.location.href);
            if (!isUpgradePage(responseUrl) || !/text\/html/i.test(response.headers.get("Content-Type") || "")) {
                window.location.assign(responseUrl.href);
                return null;
            }
            return response.text().then(function (html) {
                return { html: html, url: responseUrl };
            });
        }).then(function (result) {
            if (!result || version !== requestVersion) return;
            var pageDocument = new DOMParser().parseFromString(result.html, "text/html");
            if (!pageDocument.getElementById("pageContent")) {
                window.location.assign(result.url.href);
                return;
            }
            captureViewState(new URL(window.location.href));
            restoreViewState(pageDocument, result.url);
            if (!replacePageView(pageDocument)) {
                window.location.assign(result.url.href);
                return;
            }
            if (pushHistory) window.history.pushState({ upgradeLocalView: true }, "", result.url.href);
            document.dispatchEvent(new CustomEvent("ngxops:page-inserted"));
            window.scrollTo({ top: 0, behavior: "auto" });
            executePageScripts(pageDocument, result.url.href, function () {});
        }).catch(function () {
            if (version === requestVersion) window.location.assign(destination.href);
        });
    }

    /** 将升级页 GET 表单转换为局部页面请求。 */
    function submitGetForm(form) {
        var url = new URL(form.action || window.location.href, window.location.href);
        url.search = "";
        new FormData(form).forEach(function (value, name) {
            if (typeof value === "string") url.searchParams.append(name, value);
        });
        navigate(url.href, "GET", null, true);
    }

    /** 以局部页面请求提交已确认的软件包删除表单。 */
    function submitPostForm(form) {
        navigate(form.action || window.location.href, "POST", new FormData(form), true);
    }

    /** 拦截升级模块的同域链接，文件下载继续使用浏览器原生导航。 */
    document.addEventListener("click", function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }
        var link = event.target.closest("a[href]");
        if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
        var url = new URL(link.href, window.location.href);
        if (!isUpgradePage(url) || /\/download\/?$/.test(url.pathname)) return;
        event.preventDefault();
        navigate(url.href, "GET", null, true);
    });

    /** 以局部请求处理升级页筛选和已确认的包删除。 */
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form || !form.closest("#pageContent")) return;
        if (form.method.toUpperCase() === "GET") {
            event.preventDefault();
            submitGetForm(form);
            return;
        }
        if (!form.hasAttribute("data-upgrade-partial-post")) return;
        if (form.hasAttribute("data-confirm") && form.dataset.confirmed !== "1") return;
        delete form.dataset.confirmed;
        event.preventDefault();
        event.stopPropagation();
        submitPostForm(form);
    }, true);

    /** 浏览器前进或后退时只更新升级页面内容区。 */
    window.addEventListener("popstate", function () {
        var url = new URL(window.location.href);
        if (isUpgradePage(url)) navigate(url.href, "GET", null, false);
    });

    window.navigateUpgradePage = function (url) {
        navigate(url, "GET", null, true);
    };
})(window, document);
