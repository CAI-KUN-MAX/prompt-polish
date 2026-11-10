window.__ModuleLoader__.load({
    id: "dsh-plugin-prompt-polish",
    factory: (require) => {
        var module = { exports: {} };
        var exports = module.exports;
        Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

        const React = require("react");

        const STYLE_ID = "dsh-plugin-prompt-polish/style";
        if (typeof document !== "undefined" && document.getElementById(STYLE_ID) === null) {
            const style = document.createElement("style");
            style.id = STYLE_ID;
            style.textContent = `
.pp-opt-btn{box-sizing:border-box;width:28px;height:28px;border:0;border-radius:999px;background:transparent;color:var(--dsw-alias-label-tertiary);display:inline-flex;align-items:center;justify-content:center;padding:0;margin-right:2px;cursor:pointer;flex:none;transition:background .15s,color .15s}
.pp-opt-btn:hover{background:var(--dsw-alias-interactive-bg-hover);color:var(--dsw-alias-label-secondary)}
.pp-opt-btn:active{background:var(--dsw-alias-interactive-bg-pressed,var(--dsw-alias-interactive-bg-hover))}
.pp-opt-btn:disabled{opacity:.45;cursor:default}
.pp-opt-btn[data-error="true"]{color:var(--dsw-alias-state-error-primary);background:var(--dsw-alias-state-error-background,transparent)}
.pp-opt-spin{width:14px;height:14px;border:1.5px solid currentColor;border-right-color:transparent;border-radius:50%;animation:pp-opt-spin .8s linear infinite;display:inline-block}
@keyframes pp-opt-spin{to{transform:rotate(360deg)}}
`;
            document.head.appendChild(style);
        }

        function icon() {
            return React.createElement(
                "svg",
                {
                    viewBox: "0 0 16 16",
                    width: 16,
                    height: 16,
                    fill: "none",
                    stroke: "currentColor",
                    strokeWidth: 1.25,
                    strokeLinecap: "round",
                    strokeLinejoin: "round",
                    "aria-hidden": true,
                },
                React.createElement("path", {
                    d: "M8 1.8l1.15 3.05L12.2 6 9.15 7.15 8 10.2 6.85 7.15 3.8 6l3.05-1.15L8 1.8z",
                }),
                React.createElement("path", {
                    d: "M12.4 10.4l.55 1.45 1.45.55-1.45.55-.55 1.45-.55-1.45-1.45-.55 1.45-.55.55-1.45z",
                })
            );
        }

        function Inner(props) {
            const state = props.useInput((s) => s);
            const draftRef = React.useRef(state.draft);
            draftRef.current = state.draft;
            const lastRef = React.useRef(null);
            const timerRef = React.useRef(0);
            const [busy, setBusy] = React.useState(false);
            const [error, setError] = React.useState(null);

            React.useEffect(() => () => {
                if (timerRef.current) window.clearTimeout(timerRef.current);
            }, []);

            const showError = (message) => {
                setError(message);
                if (timerRef.current) window.clearTimeout(timerRef.current);
                timerRef.current = window.setTimeout(() => setError(null), 4000);
            };

            const onClick = async (event) => {
                if (busy) return;
                const original = draftRef.current;
                if (original.trim().length === 0) {
                    showError("草稿为空");
                    return;
                }
                if (event.shiftKey && lastRef.current !== null && original === lastRef.current.result) {
                    props.inputActions.setDraft(lastRef.current.original);
                    lastRef.current = null;
                    setError(null);
                    return;
                }
                setBusy(true);
                setError(null);
                try {
                    if ((state.occurrences?.length ?? 0) > 0 || (state.imageIds?.length ?? 0) > 0) {
                        throw new Error("草稿含引用或附件，无法安全替换");
                    }
                    const sid = String(props.sessionId ?? "");
                    const sessionId = sid.length === 0
                        ? ""
                        : (sid.startsWith("session-") ? sid : "session-" + sid);
                    if (sessionId.length === 0) throw new Error("缺少会话 ID，请刷新页面");
                    const response = await fetch("/prompt-polish/optimize", {
                        method: "POST",
                        headers: { "content-type": "application/json" },
                        body: JSON.stringify({ sessionId, draft: original }),
                    });
                    if (response.status === 404) throw new Error("插件服务未加载，请重启 DSH");
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const data = await response.json();
                    if (!data || data.ok !== true || typeof data.text !== "string") {
                        throw new Error(data && data.error && data.error.message ? data.error.message : "优化失败");
                    }
                    if (draftRef.current !== original) {
                        showError("草稿已修改，未回填");
                        return;
                    }
                    props.inputActions.setDraft(data.text);
                    lastRef.current = { original, result: data.text };
                } catch (failure) {
                    const raw = failure instanceof Error ? failure.message : String(failure);
                    const message = raw === "Failed to fetch"
                        ? "无法连接本地插件服务，请重启 DSH"
                        : raw;
                    showError(message);
                    console.warn("prompt-polish:", message);
                } finally {
                    setBusy(false);
                }
            };

            const label = "优化提示词";
            const title = error !== null ? error : "优化提示词（Shift+点击恢复上一版）";
            return React.createElement(
                "button",
                {
                    type: "button",
                    className: "pp-opt-btn",
                    "aria-label": label,
                    title,
                    "data-error": error !== null ? "true" : "false",
                    "data-busy": busy ? "true" : "false",
                    disabled: busy,
                    onMouseDown: (event) => event.preventDefault(),
                    onClick,
                },
                busy
                    ? React.createElement("span", { className: "pp-opt-spin", "aria-hidden": true })
                    : icon()
            );
        }

        function OptimizeButton(props) {
            if (typeof props.useInput !== "function" || props.inputActions === undefined) return null;
            return React.createElement(Inner, props);
        }

        const inject = ["slots"];

        function apply(ctx) {
            ctx.slots.inject("conversation.input.right", () => ctx.slots.register({
                name: "conversation.input.right",
                id: "prompt-polish",
                order: 10,
                inject: (sessionId) => ({ sessionId }),
            }, OptimizeButton));
        }

        exports.apply = apply;
        exports.inject = inject;
        return module.exports;
    },
});
