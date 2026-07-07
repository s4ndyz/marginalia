/**
 * highlighter.js — Marginalia 高亮系统
 *
 * Python → JS: page.runJavaScript("restoreHighlights([...])")
 * JS → Python: console.log("MARGINALIA_HL::" + JSON.stringify(payload))
 */

(function () {
    if (window.__marginaliaHighlighterInstalled) { return; }
    window.__marginaliaHighlighterInstalled = true;

    const MSG_PREFIX = "MARGINALIA_HL::";

    const COLORS = [
        { key: "yellow", css: "#FFE066" },
        { key: "green",  css: "#A8E6A3" },
        { key: "blue",   css: "#A3C8F5" },
        { key: "pink",   css: "#F5A3C8" },
    ];
    const DEFAULT_COLOR = COLORS[0];

    // ------------------------------------------------------------------
    // XPath 工具
    // ------------------------------------------------------------------

    function getXPath(el) {
        if (!el || el.nodeType !== Node.ELEMENT_NODE) { return ""; }
        const parts = [];
        let node = el;
        while (node && node.nodeType === Node.ELEMENT_NODE) {
            let idx = 1;
            let sib = node.previousSibling;
            while (sib) {
                if (sib.nodeType === Node.ELEMENT_NODE && sib.tagName === node.tagName) { idx++; }
                sib = sib.previousSibling;
            }
            parts.unshift(node.tagName.toLowerCase() + "[" + idx + "]");
            node = node.parentNode;
        }
        return "/" + parts.join("/");
    }

    const BLOCK_TAGS = new Set([
        "p","div","h1","h2","h3","h4","h5","h6",
        "li","blockquote","pre","td","th","section","article"
    ]);

    function nearestBlock(node) {
        let el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
        while (el && !BLOCK_TAGS.has(el.tagName.toLowerCase())) { el = el.parentElement; }
        return el || document.body;
    }

    function getTextOffset(container, targetNode, offsetInNode) {
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
        let total = 0, node;
        while ((node = walker.nextNode())) {
            if (node === targetNode) { return total + offsetInNode; }
            total += node.textContent.length;
        }
        return total;
    }

    // ------------------------------------------------------------------
    // 还原工具
    // ------------------------------------------------------------------

    function resolveXPath(xpath) {
        try {
            return document.evaluate(
                xpath, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null
            ).singleNodeValue;
        } catch (e) { return null; }
    }

    function resolveTextOffset(container, targetOffset) {
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
        let acc = 0, node;
        while ((node = walker.nextNode())) {
            const len = node.textContent.length;
            if (acc + len >= targetOffset) { return { node, offset: targetOffset - acc }; }
            acc += len;
        }
        const walker2 = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
        let last = null;
        while ((node = walker2.nextNode())) { last = node; }
        return last ? { node: last, offset: last.textContent.length } : { node: container, offset: 0 };
    }

    // ------------------------------------------------------------------
    // 核心 wrap：手动分割文本节点，可靠处理跨元素的长选区
    // ------------------------------------------------------------------

    function makeMark(colorCss, highlightId) {
        const mark = document.createElement("mark");
        mark.style.backgroundColor = colorCss;
        mark.style.borderRadius = "2px";
        mark.style.cursor = "pointer";
        mark.dataset.marginaliaId = String(highlightId);
        mark.dataset.marginaliaColor = colorCss;
        return mark;
    }

    function wrapRangeSafe(range, colorCss, highlightId) {
        const walker = document.createTreeWalker(
            range.commonAncestorContainer.nodeType === Node.TEXT_NODE
                ? range.commonAncestorContainer.parentNode
                : range.commonAncestorContainer,
            NodeFilter.SHOW_TEXT,
            null
        );

        const textNodes = [];
        let node;
        while ((node = walker.nextNode())) {
            if (range.intersectsNode(node)) { textNodes.push(node); }
        }

        const marks = [];
        for (const textNode of textNodes) {
            const nodeStart = range.startContainer === textNode ? range.startOffset : 0;
            const nodeEnd   = range.endContainer   === textNode ? range.endOffset   : textNode.textContent.length;

            if (nodeStart >= nodeEnd) { continue; }

            if (nodeEnd < textNode.textContent.length) { textNode.splitText(nodeEnd); }
            if (nodeStart > 0) {
                textNode.splitText(nodeStart);
                const toWrap = textNode.nextSibling;
                if (!toWrap) { continue; }
                const mark = makeMark(colorCss, highlightId);
                toWrap.parentNode.insertBefore(mark, toWrap);
                mark.appendChild(toWrap);
                marks.push(mark);
            } else {
                const mark = makeMark(colorCss, highlightId);
                textNode.parentNode.insertBefore(mark, textNode);
                mark.appendChild(textNode);
                marks.push(mark);
            }
        }
        return marks;
    }

    function unwrapMark(mark) {
        const parent = mark.parentNode;
        if (!parent) { return; }
        while (mark.firstChild) { parent.insertBefore(mark.firstChild, mark); }
        parent.removeChild(mark);
        parent.normalize();
    }

    function unwrapById(highlightId) {
        document.querySelectorAll(`mark[data-marginalia-id="${highlightId}"]`)
            .forEach(unwrapMark);
    }

    // ------------------------------------------------------------------
    // 气泡菜单
    // ------------------------------------------------------------------

    let currentBubble = null;

    function removeBubble() {
        if (currentBubble) { currentBubble.remove(); currentBubble = null; }
    }

    function showBubble(x, y, mode, range, highlightId, currentColorCss) {
        removeBubble();

        const bubble = document.createElement("div");
        bubble.style.cssText = [
            "position:fixed", "z-index:99999",
            "background:#fff", "border:1px solid #ddd",
            "border-radius:8px", "box-shadow:0 4px 16px rgba(0,0,0,.15)",
            "padding:6px 8px", "display:flex", "align-items:center", "gap:6px",
            "font-family:system-ui,sans-serif",
        ].join(";");

        // 4 个颜色圆点
        for (const c of COLORS) {
            const btn = document.createElement("button");
            const isActive = (mode === "existing" && c.css === currentColorCss);
            btn.style.cssText = [
                "width:20px", "height:20px", "border-radius:50%",
                "background:" + c.css,
                "border:" + (isActive ? "2.5px solid #333" : "1.5px solid rgba(0,0,0,.15)"),
                "cursor:pointer", "padding:0", "flex-shrink:0",
            ].join(";");
            btn.title = c.key;

            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                if (mode === "new") {
                    applyAndReport(range, c.css, c.key);
                } else {
                    notify({ action: "update_color", id: highlightId, color: c.key });
                    document.querySelectorAll(`mark[data-marginalia-id="${highlightId}"]`)
                        .forEach(m => {
                            m.style.backgroundColor = c.css;
                            m.dataset.marginaliaColor = c.css;
                        });
                }
                removeBubble();
                window.getSelection()?.removeAllRanges();
            });
            bubble.appendChild(btn);
        }

        // 分隔线
        const sep1 = document.createElement("span");
        sep1.style.cssText = "width:1px;height:18px;background:#e0e0e0;margin:0 2px;flex-shrink:0";
        bubble.appendChild(sep1);

        // 笔记按钮（只在已有高亮时显示）
        if (mode === "existing") {
            const noteBtn = document.createElement("button");
            noteBtn.textContent = "✎";
            noteBtn.title = "添加笔记";
            noteBtn.style.cssText = [
                "border:none", "background:transparent",
                "cursor:pointer", "color:#555", "font-size:15px", "padding:0 2px",
                "line-height:1",
            ].join(";");
            noteBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                notify({ action: "open_note", id: highlightId });
                removeBubble();
            });
            bubble.appendChild(noteBtn);

            // 分隔线
            const sep2 = document.createElement("span");
            sep2.style.cssText = "width:1px;height:18px;background:#e0e0e0;margin:0 2px;flex-shrink:0";
            bubble.appendChild(sep2);
        }

        // 删除按钮
        const delBtn = document.createElement("button");
        delBtn.textContent = "✕";
        delBtn.style.cssText = [
            "border:none", "background:transparent",
            "cursor:pointer", "color:#999", "font-size:14px", "padding:0 2px",
            "line-height:1",
        ].join(";");
        delBtn.title = "删除高亮";
        delBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (mode === "existing") {
                notify({ action: "delete", id: highlightId });
                unwrapById(highlightId);
            }
            removeBubble();
            window.getSelection()?.removeAllRanges();
        });
        bubble.appendChild(delBtn);

        document.body.appendChild(bubble);
        currentBubble = bubble;

        const bw = bubble.offsetWidth || 180;
        const bh = bubble.offsetHeight || 36;
        bubble.style.left = Math.min(x, window.innerWidth  - bw - 8) + "px";
        bubble.style.top  = Math.max(y - bh - 10, 8) + "px";
    }

    // ------------------------------------------------------------------
    // 核心：提取锚点 → 上报 Python → wrap DOM
    // ------------------------------------------------------------------

    function notify(payload) {
        console.log(MSG_PREFIX + JSON.stringify(payload));
    }

    function attachClickHandlers(marks, highlightId) {
        marks.forEach(mark => {
            mark.addEventListener("click", (e) => {
                e.stopPropagation();
                const id  = mark.dataset.marginaliaId;
                const css = mark.dataset.marginaliaColor || mark.style.backgroundColor;
                showBubble(e.clientX, e.clientY, "existing", null, id, css);
            });
        });
    }

    function applyAndReport(range, colorCss, colorKey) {
        const startBlock = nearestBlock(range.startContainer);
        const container  = startBlock;

        const containerXpath = getXPath(container);
        const startOffset    = getTextOffset(container, range.startContainer, range.startOffset);
        const endOffset      = getTextOffset(container, range.endContainer,   range.endOffset);
        const selectedText   = range.toString();

        const tempId = "pending_" + Date.now();
        const marks  = wrapRangeSafe(range, colorCss, tempId);

        notify({
            action: "create",
            containerXpath: containerXpath,
            startOffset: startOffset,
            endOffset: endOffset,
            selectedText: selectedText,
            color: colorKey,
            tempId: tempId,
        });

        attachClickHandlers(marks, tempId);
    }

    window.updateHighlightId = function (tempId, realId) {
        document.querySelectorAll(`mark[data-marginalia-id="${tempId}"]`)
            .forEach(m => { m.dataset.marginaliaId = String(realId); });
    };

    // ------------------------------------------------------------------
    // 还原已保存的高亮
    // ------------------------------------------------------------------

    window.restoreHighlights = function (highlights) {
        for (const h of highlights) {
            const container = resolveXPath(h.containerXpath);
            if (!container) { continue; }

            const start = resolveTextOffset(container, h.startOffset);
            const end   = resolveTextOffset(container, h.endOffset);

            const range = document.createRange();
            try {
                range.setStart(start.node, start.offset);
                range.setEnd(end.node, end.offset);
            } catch (_e) { continue; }

            const colorCss = h.color;
            const marks = wrapRangeSafe(range, colorCss, h.id);
            attachClickHandlers(marks, h.id);
        }
    };

    // ------------------------------------------------------------------
    // 事件监听
    // ------------------------------------------------------------------

    document.addEventListener("mouseup", (e) => {
        if (currentBubble && currentBubble.contains(e.target)) { return; }

        setTimeout(() => {
            const sel = window.getSelection();
            if (!sel || sel.isCollapsed || sel.toString().trim() === "") {
                if (!currentBubble?.contains(e.target)) { removeBubble(); }
                return;
            }
            const range = sel.getRangeAt(0).cloneRange();
            showBubble(e.clientX, e.clientY, "new", range, null, null);
        }, 10);
    });

    document.addEventListener("mousedown", (e) => {
        if (currentBubble && !currentBubble.contains(e.target)) {
            removeBubble();
        }
    });

})();
