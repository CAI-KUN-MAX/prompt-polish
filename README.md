# PromptPolish for DeepSeek Harness

An icon-only prompt optimizer for the **DeepSeek Harness** composer. Click the ✨ button immediately left of the model selector and the current draft is rewritten with the active session's model and DSH quota — no API key, no external window, no auto-send.

[![DSH Marketplace](https://img.shields.io/badge/DSH%20Marketplace-listed-4C6FFF)](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/blob/main/data/plugins/CAI-KUN-MAX__prompt-polish--dsh-plugin-prompt-polish.yml)
[![npm](https://img.shields.io/npm/v/dsh-plugin-prompt-polish)](https://www.npmjs.com/package/dsh-plugin-prompt-polish)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![PromptPolish composer button](dsh-plugin-prompt-polish/assets/screenshot-1.png)

![Optimized result in the composer](dsh-plugin-prompt-polish/assets/screenshot-2.png)

## Features

- **Icon-only composer button** registered in `conversation.input.right`, immediately left of the model selector.
- **Uses DSH quota** through `ctx.llm.stream()` with the session's current provider/model and the DSH credentials seam.
- **Replaces the draft in place** via `inputActions.setDraft()`; it never sends a message and never appends a session turn.
- **Draft-change protection**: if the draft changed while the request was in flight, the result is not applied.
- **Reference/attachment guard**: drafts with @ mention chips or image attachments are refused.
- **Shift+click restore** returns to the previous draft after a successful rewrite.
- **Clear errors** for empty drafts, missing plugin route, network failures, and model errors.

## Install

### DSH Marketplace

Already listed. Open the DSH **Plugins** panel and search for **PromptPolish** or **prompt-polish**.

### npm

```powershell
dsh plugin --profile desktop add dsh-plugin-prompt-polish
```

Package: <https://www.npmjs.com/package/dsh-plugin-prompt-polish> (current `0.1.1`).

### From source

```powershell
git clone https://github.com/CAI-KUN-MAX/prompt-polish.git
cd prompt-polish
powershell -ExecutionPolicy Bypass -File .\install-dsh-plugin.ps1
```

The installer copies the plugin into `%USERPROFILE%\.dsh\profiles\desktop\node_modules\`, backs up and updates the profile patch layer, and runs an import check.

**Restart DeepSeek Harness once** after installation; host plugins load at startup.

## Usage

1. Write a prompt in the composer.
2. Click the ✨ icon left of the model selector.
3. Wait for the spinner to finish; the draft is replaced with a structured version.
4. Review it and send manually.
5. Click again to optimize the current result further.
6. Shift+click to restore the previous draft.

No Enter, no send button, no automatic message.

## How it works

```text
client.js  ── POST /prompt-polish/optimize ──►  lib/index.js
(composer slot)   { sessionId, draft }            (host route)
      ▲                                              │
      │  inputActions.setDraft(text)                 │ ctx.sessions.get(sessionId)
      └──────────────────────────────────────────────┘ .requestHeader() → provider/model
                                                     │
                                                     └ ctx.llm.stream(...) → DSH quota
```

- `lib/index.js` resolves the session's current provider/model and streams a rewrite through the host LLM runtime.
- `lib/client.js` renders the composer-slot button, reads the live draft, and writes the result back.
- `cordis.patch.yml` is the bundle manifest patch used by the DSH plugin installer.
- No credentials are read or stored by the plugin; DSH owns authentication.

## Safety

- The host route never appends to the session log and never runs tools.
- The client refuses to replace a draft that changed during the request.
- Drafts containing reference chips or image attachments are refused.
- The plugin only rewrites the current draft; it does not read chat history.

## Verified

- `/prompt-polish/health` returns 200 after a DSH restart.
- Real click: 79-character draft → 1415-character structured prompt.
- Second click: 1415 → 1511 characters.
- No message was sent; the draft stayed in the composer.
- npm tarball verified: 8 files, `dsh.bundle`, patch, screenshots.

Shift+click restore is implemented but not covered by the automated check yet.

## Limitations

- A DSH restart is required after installing the host plugin.
- New chats without a `request/header` fall back to `deepseek-official` + `deepseek-flash`.
- Depends on DSH internal slots and `ctx.llm` APIs; may need adaptation after DSH upgrades.
- Output is collected as a single non-streaming result before it is written back.
- No history or multi-turn conversation UI; clicking again re-optimizes the current draft.

## Project layout

```text
dsh-plugin-prompt-polish/
  package.json          npm/DSH metadata, dsh.bundle + dsh.client
  cordis.patch.yml      loader entry
  lib/index.js          host route + ctx.llm call
  lib/client.js         composer slot button
  assets/               marketplace screenshots
  screenshots.json      marketplace screenshot manifest
  README.md             plugin-specific documentation
install-dsh-plugin.ps1  source installer
```

## License

[MIT](LICENSE)
