/**
 * footnotes.js — 把内联脚注/尾注挪到章末，引用处改为跳转+返回
 *
 * 背景：不少 epub 把脚注内容直接排在引用它的那段正文附近（而不是
 * 放在章节末尾），阅读体验被打断。这个脚本检测常见的脚注标记方式，
 * 把脚注内容搬到章末的"注释"区块，引用处点击平滑滚动过去，
 * 注释区块里点"返回正文"再滚回原来的位置。
 *
 * 检测规则（保守，找不到明确信号就不处理，避免误伤正常的站内链接）：
 *   - 引用链接带 epub:type="noteref"，或 class 含 footnote/endnote/fn
 *   - 或者链接目标元素带 epub:type 含 footnote/endnote/rearnote，
 *     或 class 含 footnote/endnote/fn
 *
 * 只处理"一次性静态注入"：这个脚本假设每次章节加载只跑一次，
 * 用 __marginaliaFootnotesInstalled 防止同一页面重复注入时重复处理。
 */

(function () {
    if (window.__marginaliaFootnotesInstalled) { return; }
    window.__marginaliaFootnotesInstalled = true;

    const EPUB_NS = "http://www.idpf.org/2007/ops";
    const NOTE_WORD = /(^|[\s_-])(foot|end)?note(s)?([\s_-]|$)|(^|[\s_-])fn([\s_-]|$)/i;

    function getEpubType(el) {
        return (
            el.getAttributeNS(EPUB_NS, "type") ||
            el.getAttribute("epub:type") ||
            ""
        ).toLowerCase();
    }

    function looksLikeNoteRef(link) {
        const epubType = getEpubType(link);
        if (epubType.split(/\s+/).includes("noteref")) { return true; }
        if (NOTE_WORD.test(link.className || "")) { return true; }
        return false;
    }

    function looksLikeNoteTarget(el) {
        const epubType = getEpubType(el);
        if (/(footnote|endnote|rearnote)/.test(epubType)) { return true; }
        if (NOTE_WORD.test(el.className || "")) { return true; }
        return false;
    }

    function flash(el) {
        const prevTransition = el.style.transition;
        const prevBg = el.style.backgroundColor;
        el.style.transition = "background-color 0.25s ease";
        el.style.backgroundColor = "#fff3b0";
        setTimeout(() => {
            el.style.backgroundColor = prevBg;
            setTimeout(() => { el.style.transition = prevTransition; }, 300);
        }, 700);
    }

    function smoothScrollTo(el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function processFootnotes() {
        const links = Array.from(document.querySelectorAll('a[href*="#"]'));
        const candidates = [];

        for (const link of links) {
            const href = link.getAttribute("href") || "";
            const hashIdx = href.indexOf("#");
            if (hashIdx === -1) { continue; }
            const targetId = href.slice(hashIdx + 1);
            if (!targetId) { continue; }
            const target = document.getElementById(targetId);
            if (!target) { continue; }

            if (looksLikeNoteRef(link) || looksLikeNoteTarget(target)) {
                candidates.push({ link, target, targetId });
            }
        }

        if (candidates.length === 0) { return; } // 没检测到注释标记，不做任何改动

        // 章末注释容器（首次处理时创建）
        let container = document.getElementById("marginalia-endnotes");
        if (!container) {
            container = document.createElement("section");
            container.id = "marginalia-endnotes";

            const rule = document.createElement("style");
            rule.textContent = `
                #marginalia-endnotes {
                    margin-top: 3em;
                    padding-top: 1.2em;
                    border-top: 1px solid #ddd;
                    font-size: 0.92em;
                    color: #444;
                }
                #marginalia-endnotes h3 {
                    font-size: 0.95em;
                    color: #888;
                    font-weight: 600;
                    margin-bottom: 0.8em;
                }
                .marginalia-footnote-item {
                    margin-bottom: 0.9em;
                    line-height: 1.6;
                }
                .marginalia-footnote-back {
                    text-decoration: none;
                    color: #888;
                    margin-right: 4px;
                }
                .marginalia-footnote-back:hover { color: #333; }
            `;
            document.head.appendChild(rule);

            const heading = document.createElement("h3");
            heading.textContent = "注释";
            container.appendChild(heading);
            document.body.appendChild(container);
        }

        let counter = 0;
        const processedTargets = new Set();

        for (const { link, target, targetId } of candidates) {
            if (processedTargets.has(targetId)) {
                // 同一条注释被引用多次：只搬一次内容，但每处引用都要能跳过去
            } else {
                processedTargets.add(targetId);
                counter++;

                // 原引用位置插入一个隐藏锚点，供"返回正文"精确定位
                const backAnchorId = "marginalia-backref-" + targetId;
                const backAnchor = document.createElement("span");
                backAnchor.id = backAnchorId;
                target.parentNode && target.parentNode === link.parentNode; // no-op guard
                link.parentNode.insertBefore(backAnchor, link);

                const wrapper = document.createElement("div");
                wrapper.className = "marginalia-footnote-item";
                wrapper.id = "marginalia-note-target-" + targetId;

                const backLink = document.createElement("a");
                backLink.href = "#" + backAnchorId;
                backLink.className = "marginalia-footnote-back";
                backLink.textContent = "\u21A9\uFE0E ";
                backLink.title = "返回正文";
                backLink.addEventListener("click", function (e) {
                    e.preventDefault();
                    smoothScrollTo(backAnchor);
                    flash(link);
                });

                wrapper.appendChild(backLink);
                while (target.firstChild) { wrapper.appendChild(target.firstChild); }
                target.parentNode.removeChild(target);
                container.appendChild(wrapper);
            }

            // 引用点击：跳到章末对应注释，而不是走浏览器默认的锚点跳转
            link.style.cursor = "pointer";
            link.addEventListener("click", function (e) {
                e.preventDefault();
                const wrapper = document.getElementById(
                    "marginalia-note-target-" + targetId
                );
                if (wrapper) {
                    smoothScrollTo(wrapper);
                    flash(wrapper);
                }
            });
        }
    }

    processFootnotes();
})();
