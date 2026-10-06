# Product Detail Image Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有表单变成可用商品原图生成逐模块成品图片、查看进度、重试和导出的本机应用。

**Architecture:** 保留 Node 单进程本机服务和现有页面样式，把静态资源、资产、模型适配器、任务状态与结果导出分离。图片先保存为本机草稿，只有用户触发 AI 操作才送往选定服务商；生成任务持久化并串行执行，支持重启后凭据恢复。

**Tech Stack:** Node.js 20+、原生 HTTP/fetch、浏览器原生 JS、Node `node:test`；上传解析使用 `busboy`，图片校验/缩略图使用 `sharp`，ZIP 使用 `archiver`。

**Spec:** [OpenSpec proposal](../../../openspec/changes/generate-product-detail-images/proposal.md)、[design](../../../openspec/changes/generate-product-detail-images/design.md)、`../../../openspec/changes/generate-product-detail-images/specs/{product-assets,model-connections,detail-generation}/spec.md`

## Global Constraints

- 服务只监听 `127.0.0.1`；任务数据位于非静态 `data/` 目录，SK 只在服务进程内存中。
- 每任务最多 6 张 JPEG/PNG/WebP，单张最多 10 MB；创建生成任务至少需要 1 图、1 模块、商品资料和两个可用模型角色。
- 首版全局生成并发 1、模块串行；已有成功模块不能因其他模块失败、重试或重启而丢失。
- 首版仅承诺 OpenAI Images Edits 生图适配器；其他预设先按视觉输入能力测试启用，不宣称都可生图。
- 图片只有在用户触发 AI 操作后送到服务商；页面说明服务商、图片数量与可能费用。
- 当前目录没有 Git 仓库。执行首任务时建立本机 Git 基线及 `data/` 忽略规则，之后每任务提交一次；本计划阶段不执行 Git 操作。

## Review Focus

- 文件名为 `.png` 但内容是 SVG/HTML：上传必须拒绝，不能只看扩展名；Task 2 测试。
- 图片总请求超出限制：必须返回可理解的错误且不留下半写入资产；Task 2 测试。
- 服务商返回 200 但没有有效图片字节：模块必须失败，不展示伪成品图；Task 6 测试。
- 生图请求中途服务重启且 SK 丢失：已完成模块保留，任务等待凭据；Task 5 测试。
- 用户取消与重试几乎同时发生：不能产生重复模块调用；Task 7 测试。

---

## File Map

| 路径 | 职责 |
|---|---|
| `server.js`、`server/app.js` | 启动与请求分发；`createApp()` 供测试注入端口、目录和 fetch |
| `server/routes/drafts.js`、`models.js`、`jobs.js` | HTTP 参数校验与响应，不保存业务状态 |
| `server/services/asset-store.js` | 草稿、原图、缩略图、任务资产归档和清理 |
| `server/services/profile-store.js` | 角色配置、SK 内存存储与公开配置 |
| `server/providers/vision-openai.js`、`vision-anthropic.js`、`image-openai.js` | 服务商协议适配及响应校验 |
| `server/services/job-store.js`、`job-runner.js`、`prompt-builder.js`、`exporter.js` | 持久化、队列、提示词、ZIP |
| `public/` | 迁移现有 HTML/CSS/JS（`providers.js` 暂留根目录供服务端共用）；加入任务进度和结果页脚本 |
| `tests/` | HTTP、资产、适配器、状态机、浏览器流程测试 |

### Task 1: 建立可测试服务入口与版本基线

**Files:**
- Create: `.gitignore`, `package.json`, `server/app.js`, `tests/app.test.js`
- Modify: `server.js`, `README.md`
- Move: `index.html`, `settings.html`, `styles.css`, `script.js`, `settings.js` → `public/`；`providers.js` 暂留根目录并通过现有静态路由提供

**Interfaces:**
- Produces: `createApp({ dataDir, providerFetch = fetch }): http.Server`；`server.js` 只调用它并监听端口。
- Preserves: `/`, `/index.html`, `/settings.html`, `/api/config`, `/api/test`, `/api/generate-copy` 当前行为。

- [ ] **Step 1:** 新建 `tests/app.test.js`，断言首页和设置页为 200、`/data/jobs/x/job.json` 为 404、非本机 Origin 为 403。
- [ ] **Step 2:** 运行 `node --test tests/app.test.js`，确认因 `createApp` 尚不存在而失败。
- [ ] **Step 3:** 移动静态文件、实现 `createApp({dataDir, providerFetch})` 与原路由委派；设置 `npm test` 为 `node --test tests/*.test.js`，创建忽略 `data/`、`node_modules/` 的 `.gitignore`。
- [ ] **Step 4:** 运行 `npm test` 与旧版 `node smoke-test.js`，两者均通过；按 README 命令打开首页和设置页，确认可加载。
- [ ] **Step 5:** 在 README 写明 Node 20+、安装与启动命令；执行 `git init`、基线提交，再提交本任务变更。

### Task 2: 草稿图片上传与本机资产

**Files:**
- Create: `server/services/asset-store.js`, `server/routes/drafts.js`, `tests/assets.test.js`
- Modify: `server/app.js`, `public/script.js`, `public/index.html`, `README.md`, `package.json`

**Interfaces:**
- Consumes: `createApp({dataDir, providerFetch})`。
- Produces: `createDraft(): Promise<{draftId}>`、`addImage(draftId, readable, filename): Promise<{assetId, previewUrl}>`、`removeImage(draftId, assetId): Promise<void>`、`commitDraft(draftId, jobId): Promise<Asset[]>`。
- HTTP: `POST /api/drafts`、`POST /api/drafts/:id/images`（multipart `image`）、`DELETE /api/drafts/:id/images/:assetId`。

- [ ] **Step 1:** 新建 `tests/assets.test.js`，断言 2 张有效图返回资产 ID；第 7 张、11 MB 文件、伪装 PNG 的 HTML 被拒；超限上传后磁盘没有残留临时文件。
- [ ] **Step 2:** 运行 `node --test tests/assets.test.js`，确认缺少草稿路由而失败。
- [ ] **Step 3:** 执行 `npm install busboy sharp`；用 `busboy` 流式限制请求体，用 `sharp` 解析实际格式并生成缩略图；资产只保存在 `data/drafts/<uuid>`，路径由服务端 UUID 构造；实现 24 小时过期草稿清理。
- [ ] **Step 4:** 运行 `node --test tests/assets.test.js`，全部通过；前端上传/删除改用资产 ID，浏览器验证只选择图片不会发起外部模型请求，README 记录格式和限制。
- [ ] **Step 5:** 提交本任务，确保 `data/` 没进入 Git。

### Task 3: 视觉与生图角色配置

**Files:**
- Create: `server/services/profile-store.js`, `server/routes/models.js`, `server/providers/vision-openai.js`, `server/providers/vision-anthropic.js`, `tests/models.test.js`
- Modify: `server/app.js`, `public/settings.js`, `public/settings.html`, `providers.js`, `README.md`

**Interfaces:**
- Produces: `setProfile({role, provider, baseUrl, model, apiKey})`、`getPublicProfiles(): PublicProfile[]`、`getRoleConfig(role): SecretProfile`；`VisionAdapter.analyze({images, brief, settings, signal}): Promise<{facts, unknowns, text, responseModel}>`。
- Role values: `vision`, `image`；SK 永不出现在 `PublicProfile`。

- [ ] **Step 1:** 新建 `tests/models.test.js`，断言两角色可分别配置；读取配置不含 SK；伪造视觉模型收到图片数据；Claude 收到原生 `image` 块；鉴权失败与不支持图片输入返回不同错误类别。
- [ ] **Step 2:** 运行 `node --test tests/models.test.js`，确认新角色和适配器尚不存在而失败。
- [ ] **Step 3:** 从旧 `server.js` 抽出内存配置与视觉适配器；设置页分角色展示预设、模型 ID、SK 状态和测试结果，视觉测试发送内置测试图片，生图测试留给 Task 6 的图片适配器。
- [ ] **Step 4:** 运行 `node --test tests/models.test.js`，全部通过；浏览器验证缺少视觉能力时不能启动图片分析，README 说明预设与能力测试的区别。
- [ ] **Step 5:** 提交本任务。

### Task 4: AI 帮写真实读取原图

**Files:**
- Create: `server/services/prompt-builder.js`, `tests/assist-copy.test.js`
- Modify: `server/routes/models.js`, `public/script.js`, `public/index.html`, `README.md`

**Interfaces:**
- Consumes: `VisionAdapter.analyze(...)` 和 `draftId`。
- Produces: `POST /api/assist-copy`，请求 `{draftId, source, platform, language}`，返回 `{text, facts, unknowns, provider}`。

- [ ] **Step 1:** 新建 `tests/assist-copy.test.js`，断言请求携带草稿图到模拟视觉服务商、未知规格被标“待补充”、没有图或能力不支持时报错且不替换用户原文。
- [ ] **Step 2:** 运行 `node --test tests/assist-copy.test.js`，确认新接口不存在而失败。
- [ ] **Step 3:** 实现图片加文本的文案提示词与接口；前端点击 AI 帮写前显示发送服务商和图片数，成功后允许用户编辑，失败保持原文。
- [ ] **Step 4:** 运行 `node --test tests/assist-copy.test.js` 并浏览器测试成功/失败两条路径；更新 README 的图片发送边界。
- [ ] **Step 5:** 提交本任务。

### Task 5: 持久任务与可恢复队列

**Files:**
- Create: `server/services/job-store.js`, `server/services/job-runner.js`, `server/routes/jobs.js`, `tests/jobs.test.js`
- Modify: `server/app.js`, `public/script.js`, `README.md`

**Interfaces:**
- Produces: `createJob({draftId, brief, settings, modules, visionProfile, imageProfile}): Promise<Job>`、`readJob(jobId): Promise<Job>`、`updateJob(jobId, patch): Promise<Job>`、`enqueue(jobId): void`。
- HTTP: `POST /api/jobs` 返回 `{jobId,status}`，`GET /api/jobs/:id` 返回阶段及逐模块状态；SK 不进入 `job.json`。

- [ ] **Step 1:** 新建 `tests/jobs.test.js`，断言缺少图/模块/资料/角色时拒绝；设置快照在任务创建后不变；模拟重启时成功模块保留、运行中模块转为 `waiting_credentials`。
- [ ] **Step 2:** 运行 `node --test tests/jobs.test.js`，确认任务接口不存在而失败。
- [ ] **Step 3:** 实现原子 JSON 写入与状态转换，队列全局并发 1；生成前显示服务商和预计调用次数，创建后轮询并显示阶段和模块进度。
- [ ] **Step 4:** 运行 `node --test tests/jobs.test.js`，全部通过；浏览器刷新生成页后仍能打开任务进度，README 列出任务状态。
- [ ] **Step 5:** 提交本任务。

### Task 6: 参考图生图与逐模块结果

**Files:**
- Create: `server/providers/image-openai.js`, `tests/image-generation.test.js`, `public/results.js`
- Modify: `server/services/job-runner.js`, `server/routes/models.js`, `server/routes/jobs.js`, `public/index.html`, `public/settings.js`, `README.md`

**Interfaces:**
- Produces: `ImageAdapter.generate({references, moduleBrief, size, quality, signal}): Promise<{bytes, mimeType, responseModel}>`；每个模块结果 `{moduleId,status,headline,body,imageUrl,error,attempts}`。
- Mapping: GPT Image 2 使用 `1:1 → 1024x1024`、`4:5 → 1024x1280`、`16:9 → 1536x864`；旧 GPT Image 1 系列请求其支持的竖版/横版尺寸后本机裁切至目标比例。自动比例由模块模板决定。不能满足尺寸的模型在任务前报能力错误。

- [ ] **Step 1:** 新建 `tests/image-generation.test.js`，断言模拟 OpenAI Edits 请求包含原图和模块提示词、比例与质量映射正确；返回 200 但无有效图片字节时模块失败且不生成伪图片。
- [ ] **Step 2:** 运行 `node --test tests/image-generation.test.js`，确认生图适配器尚不存在而失败。
- [ ] **Step 3:** 实现 OpenAI Images Edits 适配器、图片测试接口与逐模块保存；结果页只显示真实图片和文案，失败模块显示错误；原“本地预览”改为明确的演示状态。
- [ ] **Step 4:** 运行 `node --test tests/image-generation.test.js` 与 `npm test`，全部通过；浏览器用模拟服务商检查成功/失败混合结果，README 说明首版支持范围。
- [ ] **Step 5:** 提交本任务。

### Task 7: 重试、取消、导出与删除

**Files:**
- Create: `server/services/exporter.js`, `tests/lifecycle.test.js`
- Modify: `server/services/job-runner.js`, `server/routes/jobs.js`, `public/results.js`, `README.md`

**Interfaces:**
- Produces: `retryModule(jobId, moduleId): Promise<Job>`、`cancelJob(jobId): Promise<Job>`、`exportJob(jobId, writable): Promise<void>`、`deleteJob(jobId): Promise<void>`。
- HTTP: `POST /api/jobs/:id/retry`、`POST /api/jobs/:id/cancel`、`GET /api/jobs/:id/export`、`DELETE /api/jobs/:id`。

- [ ] **Step 1:** 新建 `tests/lifecycle.test.js`，断言只重试失败模块、取消后不再发出新生图请求、取消与重试竞态不重复调用、ZIP 包含顺序/文案/失败清单、删除后资产不可访问。
- [ ] **Step 2:** 运行 `node --test tests/lifecycle.test.js`，确认生命周期接口尚不存在而失败。
- [ ] **Step 3:** 执行 `npm install archiver`；实现状态锁与可中止请求、`archiver` ZIP、下载和删除接口；结果页添加对应按钮，README 说明保留与删除行为。
- [ ] **Step 4:** 运行 `node --test tests/lifecycle.test.js` 与 `npm test`，全部通过；浏览器验证部分完成任务可重试、下载和删除。
- [ ] **Step 5:** 提交本任务。

### Task 8: 整体验收

**Files:**
- Modify: `smoke-test.js`、`README.md`

**Interfaces:**
- Consumes: Tasks 1–7 的公开 HTTP API；不新增接口。

- [ ] **Step 1:** 扩展 `smoke-test.js`，用本机模拟服务商完成“2 图、3 模块、1 模块失败、重试、ZIP 导出”的端到端路径，断言所有图片都是可解码文件且 SK 未落盘。
- [ ] **Step 2:** 运行 `npm test`、`node smoke-test.js`、`openspec validate generate-product-detail-images --strict`，要求全部退出码为 0；再按 README 从全新服务进程启动并复现一次浏览器流程。
- [ ] **Step 3:** 记录真实服务商测试所需的用户 SK、模型权限与费用条件；在没有用户 SK 时明确标注“模拟接口已验证，真实平台未验证”，不以模拟结果替代真实连接结论。
