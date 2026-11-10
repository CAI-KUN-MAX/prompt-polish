/**
 * Host half of dsh-plugin-prompt-polish.
 *
 * A single local HTTP route accepts { sessionId, draft }, resolves the session's
 * current provider/model from its request header, and streams a rewrite through
 * `ctx.llm`. That means the request uses the signed-in DeepSeek Harness account
 * and its quota — no API key and no extra settings.
 *
 * The route never appends to the session, never sends a message and never runs
 * tools. It is a one-shot auxiliary completion with a bounded output.
 */
import { BlockAssembler, createUserMessage } from '@deepseek-ai/dsh-llm';

export const name = 'dsh-plugin-prompt-polish';
export const inject = ['webServer', 'llm', 'sessions'];

const ROUTE = '/prompt-polish/optimize';
const MAX_BODY_BYTES = 256 * 1024;
const MAX_DRAFT_CHARS = 12000;
const MAX_OUTPUT_TOKENS = 8000;
const TIMEOUT_MS = 90_000;

const SYSTEM_PROMPT = `你是资深提示词工程师。把用户草稿改写成可直接交给 AI 执行的高质量提示词，而不是同义句改写或简单排版。

必须遵守：
1. 信息保真：不得新增项目特定事实（字段名、路径、接口、业务规则、技术栈）；不得删除硬性约束。无法从原文确定的项目特定信息用“[待确认：…]”占位；通用工程做法（如分块读取、日志、重试）可以写成要求，但不得冒充用户给定的事实。
2. 结构：按“任务目标 / 输入 / 输出 / 约束 / 验收标准 / 交付物”组织，缺少信息的小节可以省略。
3. 可执行性：把含糊要求改成可检查的验收标准；原文没有的数值阈值写成“[建议确认：…]”。
4. 保留原语言、代码、路径、数字、专有名词。
5. 只输出改写后的提示词正文；不要解释、前言、结语，不要用代码块包裹全文。
6. 占位符控制在必要范围（尽量不超过 5 处），不要把整篇写成问卷。
7. 即使原文已经清楚，也要做结构重排和验收标准补全，不能只改标点。`;

function json(res, status, value) {
    const body = JSON.stringify(value);
    res.writeHead(status, {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
        'content-length': Buffer.byteLength(body),
    });
    res.end(body);
}

function readBody(req) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let size = 0;
        req.on('data', (chunk) => {
            size += chunk.length;
            if (size > MAX_BODY_BYTES) {
                reject(new Error('BODY_TOO_LARGE'));
                req.destroy();
                return;
            }
            chunks.push(chunk);
        });
        req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
        req.on('error', reject);
    });
}

function resolveRoute(ctx, sessionId) {
    const session = ctx.sessions.get(sessionId);
    if (session === undefined) throw new Error('SESSION_NOT_FOUND');
    const header = session.requestHeader();
    const config = header?.config;
    const provider = typeof config?.provider === 'string' && config.provider.length > 0
        ? config.provider
        : 'deepseek-official';
    const model = typeof config?.model === 'string' && config.model.length > 0
        ? config.model
        : 'deepseek-flash';
    const reasoningEffort = typeof config?.reasoningEffort === 'string' && config.reasoningEffort.length > 0
        ? config.reasoningEffort
        : undefined;
    return { sessionId: session.id, provider, model, reasoningEffort };
}

async function rewrite(ctx, route, draft) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
        const messages = [
            createUserMessage({
                content: [{ type: 'text', text: `请优化下面的提示词：\n<<<PROMPT\n${draft}\nPROMPT>>>` }],
                source: { kind: 'plugin', plugin: name },
            }),
        ];
        const assembler = new BlockAssembler();
        for await (const chunk of ctx.llm.stream({
            provider: route.provider,
            model: route.model,
            reasoningEffort: route.reasoningEffort,
            messages,
            system: SYSTEM_PROMPT,
            temperature: 0.4,
            maxTokens: MAX_OUTPUT_TOKENS,
            sessionId: route.sessionId,
            signal: controller.signal,
        })) {
            assembler.push(chunk);
        }
        const finish = assembler.finish;
        if (finish !== undefined && (finish.kind === 'error' || finish.kind === 'aborted')) {
            const failure = finish.failure;
            throw Object.assign(new Error(failure?.message || 'MODEL_FAILED'), {
                code: failure?.code || (finish.kind === 'aborted' ? 'ABORTED' : 'MODEL_FAILED'),
            });
        }
        const text = assembler.blocks()
            .filter((block) => block.type === 'text')
            .map((block) => block.text)
            .join('')
            .trim();
        if (text.length === 0) throw Object.assign(new Error('EMPTY_RESPONSE'), { code: 'EMPTY_RESPONSE' });
        return text;
    } finally {
        clearTimeout(timer);
    }
}

export function apply(ctx) {
    ctx.effect(() => ctx.webServer.register({
        kind: 'exact',
        path: '/prompt-polish/health',
        handler: (req, res) => {
            json(res, 200, { ok: true, plugin: name, version: '0.1.0' });
        },
    }), 'prompt-polish: health route');

    ctx.effect(() => ctx.webServer.register({
        kind: 'exact',
        path: ROUTE,
        handler: async (req, res) => {
            if (req.method === 'OPTIONS') {
                res.writeHead(204, {
                    'access-control-allow-origin': '*',
                    'access-control-allow-methods': 'POST, OPTIONS',
                    'access-control-allow-headers': 'content-type',
                });
                res.end();
                return;
            }
            if (req.method !== 'POST') {
                json(res, 405, { ok: false, error: { code: 'METHOD_NOT_ALLOWED', message: 'POST only' } });
                return;
            }
            try {
                const raw = await readBody(req);
                let body;
                try {
                    body = JSON.parse(raw);
                } catch {
                    json(res, 400, { ok: false, error: { code: 'BAD_JSON', message: '请求体不是 JSON' } });
                    return;
                }
                const sessionId = typeof body?.sessionId === 'string' ? body.sessionId : '';
                const draft = typeof body?.draft === 'string' ? body.draft : '';
                if (sessionId.length === 0) {
                    json(res, 400, { ok: false, error: { code: 'NO_SESSION', message: '缺少 sessionId' } });
                    return;
                }
                if (draft.trim().length === 0) {
                    json(res, 400, { ok: false, error: { code: 'EMPTY_DRAFT', message: '草稿为空' } });
                    return;
                }
                if (draft.length > MAX_DRAFT_CHARS) {
                    json(res, 400, {
                        ok: false,
                        error: { code: 'DRAFT_TOO_LONG', message: `草稿超过 ${MAX_DRAFT_CHARS} 字符` },
                    });
                    return;
                }
                const route = resolveRoute(ctx, sessionId);
                const text = await rewrite(ctx, route, draft);
                json(res, 200, {
                    ok: true,
                    text,
                    model: route.model,
                    provider: route.provider,
                });
            } catch (error) {
                const code = typeof error?.code === 'string' ? error.code : 'FAILED';
                const message = error?.code === 'SESSION_NOT_FOUND'
                    ? '当前会话不存在或已关闭'
                    : typeof error?.message === 'string'
                        ? error.message
                        : '优化失败';
                ctx.logger?.warn?.(`prompt-polish: ${code}`);
                json(res, 200, { ok: false, error: { code, message } });
            }
        },
    }), 'prompt-polish: optimize route');
}
