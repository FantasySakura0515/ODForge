# odforge

ODForge 的 Python 套件：CLI（`odforge`）、MCP server（`odforge-mcp`）與網頁編輯器。
讓 AI 助理直接產出原生 ODF 簡報、文件、試算表（`.odp`／`.odt`／`.ods`），並在瀏覽器裡跟你來回修改。

## 安裝

```bash
uv tool install "odforge[web,cjk]"   # 或 pipx install "odforge[web,cjk]"
odforge setup                        # 接上這台電腦的 Claude Code 與 Codex
odforge init 我的簡報                 # 或只設定某個資料夾：放 AGENTS.md、skill 與 .mcp.json
```

只用 Claude Code 的話，也可以直接裝 plugin：

```text
/plugin marketplace add FantasySakura0515/ODForge
/plugin install odforge@odforge
```

建議另外裝 [LibreOffice](https://www.libreoffice.org/)，預覽圖和第三道驗證閘會用到它（只在背景執行）。

完整用法見 GitHub 上的 README：[繁體中文](https://github.com/FantasySakura0515/ODForge/blob/main/README.md) |
[English](https://github.com/FantasySakura0515/ODForge/blob/main/README.en.md)。

- 驗證閘的定義：[docs/gates.md](https://github.com/FantasySakura0515/ODForge/blob/main/odforge/docs/gates.md)
- Web API：[docs/webapi.md](https://github.com/FantasySakura0515/ODForge/blob/main/odforge/docs/webapi.md)
- `odforge benchmark` 報告格式：[docs/benchmark.md](https://github.com/FantasySakura0515/ODForge/blob/main/odforge/docs/benchmark.md)
- 環境變數範本：[.env.example](https://github.com/FantasySakura0515/ODForge/blob/main/odforge/.env.example)

## 授權

MIT。
