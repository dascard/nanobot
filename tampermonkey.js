// ==UserScript==
// @name         Nanobot Gemini Injector
// @namespace    http://tampermonkey.net/
// @version      0.2
// @description  自进化 Agent 注入器：向 Gemini 官方网页注入画像设定文件，并自动同步聊天日志
// @match        https://gemini.google.com/app*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      localhost
// @connect      *
// ==/UserScript==

(function () {
    'use strict';

    // ═══════════════════════════════════════
    // 用户配置区（按需修改）
    // ═══════════════════════════════════════
    const USER_ID  = GM_getValue("nanobot_user_id", "default_user");
    const API_BASE = GM_getValue("nanobot_api_base", "http://localhost:8000/api/v1");
    const LOG_POLL_INTERVAL = 5000; // 日志轮询间隔（毫秒）

    // ═══════════════════════════════════════
    // 浮动控制面板
    // ═══════════════════════════════════════
    const panel = document.createElement("div");
    panel.id = "nanobot-panel";
    panel.innerHTML = `
        <div style="position:fixed;bottom:20px;right:20px;z-index:99999;
                    background:rgba(30,30,30,0.92);color:#eee;
                    padding:12px 16px;border-radius:12px;
                    box-shadow:0 8px 24px rgba(0,0,0,0.3);
                    font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;
                    min-width:220px;backdrop-filter:blur(8px);">
            <div style="font-weight:600;margin-bottom:8px;">🧠 Nanobot Injector</div>
            <button id="nb-inject" style="background:#4CAF50;color:#fff;border:none;
                    padding:6px 14px;border-radius:6px;cursor:pointer;width:100%;
                    font-size:13px;transition:background 0.2s;">
                🚀 注入设定文件
            </button>
            <div id="nb-status" style="margin-top:8px;font-size:11px;color:#aaa;">
                状态：待机中
            </div>
        </div>
    `;
    document.body.appendChild(panel);

    const statusEl = document.getElementById("nb-status");
    const injectBtn = document.getElementById("nb-inject");

    function setStatus(msg, color = "#aaa") {
        statusEl.textContent = `状态：${msg}`;
        statusEl.style.color = color;
    }

    // ═══════════════════════════════════════
    // 注入逻辑：拉取画像 → 生成虚拟文件 → 拖入输入框
    // ═══════════════════════════════════════
    injectBtn.addEventListener("click", () => {
        setStatus("正在拉取设定...", "#FFD54F");
        GM_xmlhttpRequest({
            method: "GET",
            url: `${API_BASE}/context?user_id=${encodeURIComponent(USER_ID)}`,
            onload(resp) {
                if (resp.status !== 200) {
                    setStatus(`服务器错误 ${resp.status}`, "#EF5350");
                    return;
                }
                try {
                    const data = JSON.parse(resp.responseText);
                    const fileContent = [
                        "=== NANOBOT SYSTEM CONFIGURATION ===",
                        "",
                        "## System Prompt",
                        data.system_prompt || "(empty)",
                        "",
                        "## User Persona",
                        data.persona_json || "{}",
                        "",
                        "=== END CONFIG ===",
                    ].join("\n");

                    const file = new File([fileContent], "Nanobot_Config.txt", { type: "text/plain" });
                    injectFile(file);
                } catch (e) {
                    setStatus("解析失败: " + e.message, "#EF5350");
                    console.error("[Nanobot]", e);
                }
            },
            onerror(err) {
                setStatus("连接服务器失败", "#EF5350");
                console.error("[Nanobot] Connection error:", err);
            },
        });
    });

    function injectFile(file) {
        // 方案 A：尝试找到隐藏的 <input type="file"> 并直接赋值
        const fileInputs = document.querySelectorAll('input[type="file"]');
        for (const input of fileInputs) {
            try {
                const dt = new DataTransfer();
                dt.items.add(file);
                input.files = dt.files;
                input.dispatchEvent(new Event("change", { bubbles: true }));
                setStatus("✅ 文件已注入！请输入指令发送", "#66BB6A");
                prefillPrompt();
                return;
            } catch (e) {
                // 某些 input 可能 readonly，跳过
            }
        }

        // 方案 B：模拟拖放到聊天输入区域
        const dropZone = document.querySelector(
            'rich-textarea, .ql-editor, [contenteditable="true"], textarea, .input-area'
        );
        if (dropZone) {
            const dt = new DataTransfer();
            dt.items.add(file);
            dropZone.dispatchEvent(new DragEvent("dragenter", { bubbles: true, dataTransfer: dt }));
            dropZone.dispatchEvent(new DragEvent("dragover",  { bubbles: true, dataTransfer: dt }));
            dropZone.dispatchEvent(new DragEvent("drop",      { bubbles: true, cancelable: true, dataTransfer: dt }));
            setStatus("✅ 文件已拖入！请输入指令发送", "#66BB6A");
            prefillPrompt();
        } else {
            setStatus("⚠️ 未找到输入框，请手动上传文件", "#FFD54F");
        }
    }

    function prefillPrompt() {
        // 尝试在输入框中预填文字
        setTimeout(() => {
            const editor = document.querySelector(
                'rich-textarea .ql-editor, [contenteditable="true"], textarea'
            );
            if (editor) {
                const hint = "请阅读附件中的系统设定作为本次对话的底层规则。回复「收到」即可。";
                if (editor.tagName === "TEXTAREA") {
                    editor.value = hint;
                    editor.dispatchEvent(new Event("input", { bubbles: true }));
                } else {
                    editor.textContent = hint;
                    editor.dispatchEvent(new Event("input", { bubbles: true }));
                }
            }
        }, 500);
    }

    // ═══════════════════════════════════════
    // 日志收集：轮询 DOM 抓取新消息
    // ═══════════════════════════════════════
    const sentHashes = new Set();

    function hashText(text) {
        // 简易哈希：取前 80 字符 + 长度组合，避免 index 漂移问题
        return (text.substring(0, 80) + "||" + text.length).replace(/\s+/g, " ");
    }

    function collectLogs() {
        // Gemini 的消息容器选择器（可能随 Google 改版变化）
        const allTurns = document.querySelectorAll(
            'message-content, .conversation-turn, .message-row, [data-message-id]'
        );

        allTurns.forEach((el) => {
            const text = (el.innerText || "").trim();
            if (text.length < 2) return;

            const h = hashText(text);
            if (sentHashes.has(h)) return;

            // 角色判断：优先读 DOM 属性，fallback 读 class
            let role = "model";
            const parent = el.closest("[data-is-user], [data-author-role], .user-query, .model-response-text");
            if (parent) {
                if (parent.matches("[data-is-user], .user-query, [data-author-role='user']")) {
                    role = "user";
                }
            }

            GM_xmlhttpRequest({
                method: "POST",
                url: `${API_BASE}/log`,
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify({ user_id: USER_ID, role, content: text }),
                onload(resp) {
                    try {
                        const r = JSON.parse(resp.responseText);
                        setStatus(`日志 ${r.unprocessed_logs}/${GM_getValue("nanobot_threshold", 20)} 条`);
                    } catch (e) { /* ignore */ }
                },
            });

            sentHashes.add(h);
        });
    }

    setInterval(collectLogs, LOG_POLL_INTERVAL);

})();
