/**
 * highlighter.js — Marginalia 高亮系统
 *
 * Python → JS: page.runJavaScript("restoreHighlights([...])")
 * JS → Python: console.log("MARGINALIA_HL::" + JSON.stringify(payload))
 */

(function () {
    if (window.__marginaliaHighlighterInstalled) { return; }
    window.__marginaliaHighlighterInstalled = true;

    const MSG_PREFIX  = "MARGINALIA_HL::";
    const HL_COLOR    = "#FFE066";   // 唯一颜色：黄色
    const HL_COLOR_KEY = "yellow";

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
    // 还原工具：XPath → DOM 节点，textContent 偏移 → {node, offset}
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
        const last = container.lastChild || container;
        return { node: last, offset: 0 };
    }

    // ------------------------------------------------------------------
    // DOM wrap / unwrap
    // ------------------------------------------------------------------

    function wrapRange(range, highlightId) {
        const mark = document.createElement("mark");
        mark.style.backgroundColor = HL_COLOR;
        mark.style.borderRadius = "2px";
        mark.style.cursor = "pointer";
        mark.dataset.marginaliaId = String(highlightId);
        try {
            range.surroundContents(mark);
        } catch (_e) {
            const fragment = range.extractContents();
            mark.appendChild(fragment);
            range.insertNode(mark);
        }
        return mark;
    }

    function unwrapMark(mark) {
        const parent = mark.parentNode;
        if (!parent) { return; }
        while (mark.firstChild) { parent.insertBefore(mark.firstChild, mark); }
        parent.removeChild(mark);
        parent.normalize(); // 合并相邻文本节点，保持 DOM 整洁
    }

    // ------------------------------------------------------------------
    // 气泡菜单（只有删除按钮）
    // ------------------------------------------------------------------

    let currentBubble = null;

    function removeBubble() {
        if (currentBubble) { currentBubble.remove(); currentBubble = null; }
    }

    /**
     * @param x, y       鼠标位置
     * @param mode       "new" | "existing"
     * @param range      mode==="new" 时的选区
     * @param mark       mode==="existing" 时的 <mark> 元素
     */
    function showBubble(x, y, mode, range, mark) {
        removeBubble();

        const bubble = document.createElement("div");
        bubble.style.cssText = [
            "position:fixed", "z-index:99999",
            "background:#fff", "border:1px solid #ddd",
            "border-radius:8px", "box-shadow:0 4px 16px rgba(0,0,0,.15)",
            "padding:5px 10px", "display:flex", "align-items:center", "gap:8px",
            "font-family:system-ui,sans-serif", "font-size:13px",
        ].join(";");

        if (mode === "new") {
            // ── 新选区：高亮按钮 + 取消 ──
            const hlBtn = document.createElement("button");
            hlBtn.textContent = "高亮";
            hlBtn.style.cssText = [
                "border:none", "border-radius:4px", "padding:3px 10px",
                "background:" + HL_COLOR, "cursor:pointer", "font-size:13px",
            ].join(";");
            hlBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                applyAndReport(range);
                removeBubble();
                window.getSelection()?.removeAllRanges();
            });

            const cancelBtn = document.createElement("button");
            cancelBtn.textContent = "取消";
            cancelBtn.style.cssText = [
                "border:none", "background:transparent",
                "cursor:pointer", "color:#888", "font-size:13px",
            ].join(";");
            cancelBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                removeBubble();
                window.getSelection()?.removeAllRanges();
            });

            bubble.appendChild(hlBtn);
            bubble.appendChild(cancelBtn);

        } else {
            // ── 已有高亮：只有删除按钮 ──
            const delBtn = document.createElement("button");
            delBtn.textContent = "删除高亮";
            delBtn.style.cssText = [
                "border:none", "background:transparent",
                "cursor:pointer", "color:#c00", "font-size:13px",
            ].join(";");
            delBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                // 从 dataset 实时读取 id（此时已经是 Python 写库后的真实 id）
                const realId = mark.dataset.marginaliaId;
                notify({ action: "delete", id: realId });
                unwrapMark(mark);
                removeBubble();
            });
            bubble.appendChild(delBtn);
        }

        document.body.appendChild(bubble);
        currentBubble = bubble;

        // 定位气泡，避免超出视口
        const bw = bubble.offsetWidth || 120;
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

    function applyAndReport(range) {
        const startBlock = nearestBlock(range.startContainer);
        const endBlock   = nearestBlock(range.endContainer);
        const container  = (startBlock === endBlock) ? startBlock : startBlock;

        const containerXpath = getXPath(container);
        const startOffset    = getTextOffset(container, range.startContainer, range.startOffset);
        const endOffset      = getTextOffset(container, range.endContainer,   range.endOffset);
        const selectedText   = range.toString();

        // 先 wrap DOM 给立即视觉反馈，tempId 用时间戳占位
        const tempId = "pending_" + Date.now();
        const mark = wrapRange(range, tempId);

        notify({
            action: "create",
            containerXpath: containerXpath,
            startOffset: startOffset,
            endOffset: endOffset,
            selectedText: selectedText,
            color: HL_COLOR_KEY,
            tempId: tempId,
        });

        // 关键：click 事件里从 mark.dataset 实时读 id，
        // 而不是从闭包捕获 tempId——这样 updateHighlightId 更新 dataset 后，
        // 点击时能拿到真实的数字 id，删除才能正常工作
        mark.addEventListener("click", (e) => {
            e.stopPropagation();
            showBubble(e.clientX, e.clientY, "existing", null, mark);
        });
    }

    // Python 保存成功后，把 tempId 替换成真实数据库 id
    window.updateHighlightId = function (tempId, realId) {
        const mark = document.querySelector(`mark[data-marginalia-id="${tempId}"]`);
        if (mark) { mark.dataset.marginaliaId = String(realId); }
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

            const mark = wrapRange(range, h.id);
            mark.addEventListener("click", (e) => {
                e.stopPropagation();
                // 同样从 dataset 实时读，保持一致
                showBubble(e.clientX, e.clientY, "existing", null, mark);
            });
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
            showBubble(e.clientX, e.clientY, "new", range, null);
        }, 10);
    });

    document.addEventListener("mousedown", (e) => {
        if (currentBubble && !currentBubble.contains(e.target)) {
            removeBubble();
        }
    });

})();
