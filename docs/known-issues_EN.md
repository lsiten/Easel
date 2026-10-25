# Known Issues

This page tracks known issues relevant to using Easel, along with recommended workarounds. If you hit something not listed here, please open an [Issue](https://github.com/ZJU-REAL/Easel/issues).

---

## Duplicate reply after an "ask_user" prompt in legacy CLI chat (historical)

- **Historical scope**: the legacy `easel chat` terminal integration only. **The Web workspace is unaffected.**
- **Symptom**: After the Agent raises an `ask_user` prompt and you answer it, the Agent's next reply may be rendered twice in the terminal (the content is correct — it just shows up twice).
- **Nature**: This is a **display-only** issue. It does not affect the actual conversation content, asset generation, or publishing results.

### Status (legacy integration removed)

The issue lived in the **session projection** logic of an earlier agent base: `easel chat` used to call its terminal TUI, and the session projection rebuilt the transcript in the terminal. When a live reply was mistakenly matched against the `ask_user` prompt row and the two did not share a transcript identity, the projection re-inserted the reply, causing the duplicate render.

The supported engines are Codex / OpenCode / ACP / Pi, plus chat-only mode (`api`, model API direct). They do not use that terminal TUI path. If a current engine renders duplicate replies, report the selected engine and reproduction steps separately.

### Recommendation

- **Recommended**: use the **Web workspace** (`easel web`, `http://localhost:7860` by default). The Web backend renders from the raw event stream itself, offering a more complete experience (conversations, assets, accounts, profiles, content library, and publishing management) than the CLI.
