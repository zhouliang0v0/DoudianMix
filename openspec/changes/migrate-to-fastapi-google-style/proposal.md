# Proposal

## Why

当前本机应用的服务端使用 Node.js，项目目标已明确改为 Python 3.11 与 FastAPI。迁移需要在保留页面、接口和已有草稿/任务数据的前提下完成，同时把前后端代码质量检查统一到适用的 Google 风格指南。

## What Changes

- 逐模块把本机服务、模型适配器、任务队列和文件存储迁至 Python 3.11/FastAPI；开发期间在独立端口验收，最终由 Python 服务独立监听 `127.0.0.1:8765`。
- 保持现有原生 HTML/CSS/JavaScript 页面、`/api/*` 请求与响应、任务状态和 `data/` 文件格式可用；旧草稿和任务可由新服务读取与继续处理。
- 为 Python、JavaScript、HTML、CSS 引入适用的 Google 风格检查及人工审查，并把测试、启动和验收命令写入项目文档。
- **BREAKING**：最终启动方式从 `npm start` 改为 Python 3.11；切换验收通过后移除 Node 服务端代码和依赖。

## Capabilities

### New Capabilities

- `local-fastapi-runtime`: Python 3.11 本机服务的启动、现有 HTTP 契约和文件数据兼容，以及任务恢复行为。

### Modified Capabilities

无。项目主规范目录尚无已发布能力；现有 `generate-product-detail-images` 是仍在进行的独立变更，本次不修改其尚未完成的真实 SK 验证。

## Impact

- 服务端：替换 `server.js`、`server/`、Node 测试和服务端依赖，新增 FastAPI 路由、业务服务、模型适配器及 pytest 测试。
- 前端：整理 `public/` 中的原生 JavaScript、HTML、CSS；保持三页操作与同源调用方式。
- 数据与外部服务：保留 `data/drafts/`、`data/jobs/` 和 SK 仅驻留进程内存的规则；继续只在用户触发 AI 操作时向所选服务商发送商品图和资料。
- 运行：本机需要 Python 3.11；切换前备份数据并确认 Node 与 FastAPI 不同时写生产目录。
