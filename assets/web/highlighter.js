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
        // fallback：偏移超出，指向最后一个文本节点末尾
        const walker2 = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
        let last = null;
        while ((node = walker2.nextNode())) { last = node; }
        return last ? { node: last, offset: last.textContent.length } : { node: container, offset: 0 };
    }

    // ------------------------------------------------------------------
    // 核心 wrap：手动分割文本节点，可靠处理跨元素的长选区
    //
    // surroundContents() 的问题：如果选区"切了"某个内联元素（比如 <em>、<a>）
    // 的一半，它直接抛异常。fallback 的 extractContents+insertNode 会把内容
    // 从 DOM 里挖走再插回来，时机稍有偏差就在原位留下空节点，渲染成换行。
    //
    // 正确做法：用 TreeWalker 遍历选区内的所有文本节点，在起点/终点处
    // 切割文本节点，然后给每段文本套上一个独立的 <mark>。
    // 这样不移动任何节点，只在原地插入新元素，不会引起换行问题。
    // ------------------------------------------------------------------

    function makeMark(colorCss, highlightId) {
        const mark = document.createElement("mark");
        mark.style.backgroundColor = colorCss;
        mark.style.borderRadius = "2px";
        mark.style.cursor = "pointer";
        mark.dataset.marginaliaId = String(highlightId);
        return mark;
    }

    /**
     * 把一个 Range 里的所有文本用 <mark> 包裹起来。
     * 返回创建的所有 <mark> 元素（一个选区可能跨多个文本节点，
     * 会生成多个 <mark>，但它们共享同一个 highlightId）。
     */
    function wrapRangeSafe(range, colorCss, highlightId) {
        // 用 TreeWalker 收集选区内所有文本节点
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
            // 只要文本节点和选区有重叠，就纳入处理范围
            if (range.intersectsNode(node)) {
                textNodes.push(node);
            }
        }

        const marks = [];
        for (const textNode of textNodes) {
            const nodeStart = range.startContainer === textNode ? range.startOffset : 0;
            const nodeEnd   = range.endContainer   === textNode ? range.endOffset   : textNode.textContent.length;

            if (nodeStart >= nodeEnd) { continue; } // 空交集，跳过

            // 从文本节点末尾开始切（先切尾再切头，避免偏移失效）
            if (nodeEnd < textNode.textContent.length) {
                textNode.splitText(nodeEnd);
            }
            if (nodeStart > 0) {
                textNode.splitText(nodeStart);
                // splitText 返回后半段，前半段还是 textNode，后半段是新节点
                // 我们要 wrap 的是后半段
                const toWrap = textNode.nextSibling;
                if (!toWrap) { continue; }
                const mark = makeMark(colorCss, highlightId);
                toWrap.parentNode.insertBefore(mark, toWrap);
                mark.appendChild(toWrap);
                marks.push(mark);
            } else {
                // 整个文本节点都在选区内
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

    /** 删除某个 highlightId 对应的所有 <mark>（一个高亮可能跨多个文本节点）*/
    function unwrapById(highlightId) {
        const marks = document.querySelectorAll(`mark[data-marginalia-id="${highlightId}"]`);
        marks.forEach(unwrapMark);
    }

    // ------------------------------------------------------------------
    // 气泡菜单
    // ------------------------------------------------------------------

    let currentBubble = null;

    function removeBubble() {
        if (currentBubble) { currentBubble.remove(); currentBubble = null; }
    }

    /**
     * @param x, y         鼠标位置
     * @param mode         "new" | "existing"
     * @param range        mode==="new" 时的选区 Range
     * @param highlightId  mode==="existing" 时的高亮 id（string）
     * @param currentColorCss  mode==="existing" 时当前颜色 CSS 值，用于描边
     */
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

        // 4 个颜色圆点（新选区和已有高亮都显示）
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
                    // 更新颜色：通知 Python，更新 DOM 里所有对应 mark 的背景色
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
        const sep = document.createElement("span");
        sep.style.cssText = "width:1px;height:18px;background:#e0e0e0;margin:0 2px;flex-shrink:0";
        bubble.appendChild(sep);

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
            if (mode === "new") {
                // 还没创建，直接取消
            } else {
                notify({ action: "delete", id: highlightId });
                unwrapById(highlightId);
            }
            removeBubble();
            window.getSelection()?.removeAllRanges();
        });
        bubble.appendChild(delBtn);

        document.body.appendChild(bubble);
        currentBubble = bubble;

        // 定位气泡，避免超出视口
        const bw = bubble.offsetWidth || 160;
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
                // 从 dataset 实时读取（updateHighlightId 更新后自动正确）
                const id  = mark.dataset.marginaliaId;
                const css = mark.style.backgroundColor;
                showBubble(e.clientX, e.clientY, "existing", null, id, css);
            });
        });
    }

    function applyAndReport(range, colorCss, colorKey) {
        const startBlock = nearestBlock(range.startContainer);
        const endBlock   = nearestBlock(range.endContainer);
        const container  = (startBlock === endBlock) ? startBlock : startBlock;

        const containerXpath = getXPath(container);
        const startOffset    = getTextOffset(container, range.startContainer, range.startOffset);
        const endOffset      = getTextOffset(container, range.endContainer,   range.endOffset);
        const selectedText   = range.toString();

        const tempId = "pending_" + Date.now();
        const marks  = wrapRangeSafe(range, colorCss, tempId);
        marks.forEach(m => m.dataset.marginaliaColor = colorCss);

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

    // Python 保存成功后，把所有 tempId 替换成真实数据库 id
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

            const colorCss = h.color; // Python 传来的已是 CSS 色值
            const marks = wrapRangeSafe(range, colorCss, h.id);
            marks.forEach(m => m.dataset.marginaliaColor = colorCss);
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
