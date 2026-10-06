# 电商商品详情图生成器

本机单用户应用：上传商品原图、填写真实商品资料、选择详情模块，由视觉模型理解原图，再用图片编辑模型逐模块生成成品图和文案。页面沿用原有移动端表单样式。

## 启动与检查

生产运行需要 Python 3.11 与 uv。在项目目录运行：

```powershell
uv python install 3.11
uv sync --locked --python 3.11
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8765 --workers 1
```

打开 <http://127.0.0.1:8765/>。后端为 FastAPI，服务仅监听 `127.0.0.1`，必须保持一个 Uvicorn worker。生产安装与启动无需 Node.js 或 npm。Python 要求 `>=3.11,<3.12`，其他 Python 版本被版本约束拒绝。

## 开发与检查

安装 Python 检查工具，以及用于前端 lint 的 Node/npm 开发依赖：

```powershell
uv sync --locked --python 3.11
npm ci
```

开发和测试通过 `create_app(data_dir=临时目录)` 注入独立数据目录；启动第二个进程时必须使用不同端口和数据目录，同一 `data/` 只能由一个服务进程写入。Node.js 仅用于开发检查，版本范围见 `package.json`。

```powershell
uv run --locked pytest
uv run --locked pyink --check backend tests
uv run --locked pylint --rcfile=config/google.pylintrc backend
npm run lint
```

`config/google.pylintrc` 固定保存 Google styleguide 官方配置，Python 格式检查使用 Pyink 的 80 字符行宽。

统一质量门可用一条命令复现：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

需先执行 `uv sync --locked --python 3.11` 与 `npm ci` 安装开发依赖。
脚本依次检查 Pyink、Google Pylint、完整 pytest 和前端 lint，任一步失败
立即以该步骤的非零退出码结束。`-ExecutionPolicy Bypass` 仅用于本次子进程，
不修改本机策略；允许脚本执行的环境也可直接运行
`powershell -File scripts/check.ps1`。pytest 包含本机随机端口、临时数据目录的
模拟视觉/生图 HTTP 端到端测试，无需真实 SK 或外部账户。验收结果与真实
服务商未验证的边界见 [本机验收记录](docs/verification.md)。

Python 草稿资产层 `backend/asset_store.py` 已支持旧格式：
`data/drafts/<UUID>/draft.json` 保留 `id`、毫秒时间戳 `createdAt`、
`images`，图片记录保留 `assetId`、`originalName`、`previewName`、
`mimeType`、`filename`。原图字节不转码；复制任务时原图和预览进入
`data/jobs/<UUID>/originals/`。这些文件仅保存在本机，不是静态网页资源。
上传按实际内容完整解码，仅接受 JPEG、PNG、WebP，每张最多
10 × 1024 × 1024 字节、8000 万解码像素，每草稿最多 6 张；文件名最多
150 字符。预览按 EXIF 转正，等比例缩放为最长边 400 像素的 WebP
（小图也放大，质量 75），保留原图。资产和 JSON 使用临时文件加原子替换，
上传失败清理新文件；只有创建时间超过 24 小时的草稿被过期清理，任务不受影响。
HTTP 草稿接口与存储层均通过临时目录测试，沿用旧格式。

## 后端程序代码

后端入口为 `backend/main.py`。生产运行使用用户在设置页填写的模型地址和 SK，自动化测试使用本机模拟服务商。

| 文件 | 职责 |
| --- | --- |
| `backend/main.py` | 创建 FastAPI 服务、限制本机访问、分发 API 与静态页面 |
| `backend/api/drafts.py`、`backend/asset_store.py` | 接收、校验和保存商品原图及缩略图 |
| `backend/api/models.py`、`backend/profile_store.py` | 保存模型角色与 SK、执行图片能力测试、AI 帮写 |
| `backend/providers/` | 向视觉模型和图片编辑接口发送真实请求，处理超时与服务商响应 |
| `backend/api/jobs.py`、`backend/job_runner.py` | 创建任务、调用模型、更新进度、取消和单模块重试 |
| `backend/job_store.py`、`backend/exporter.py` | 持久化任务与导出 ZIP |

页面代码位于 `public/`，通过 `/api/*` 与上述后端通信。商品原图和任务结果保存在本机 `data/`，该目录不会作为静态网页开放。

完整的模块关系、数据流、状态机和安全边界见 [系统设计文档](docs/system-design.md)。

切换验收与可执行回退步骤见 [FastAPI 切换记录](docs/fastapi-cutover.md)。2026-10-06 切换时生产 `data/` 为空，已备份空目录；没有真实旧记录可验收，旧草稿、已完成任务与凭据恢复使用隔离的合成旧格式样本验证。接口对照采用清理前从 Node 提交 `9cbfba1` 抓取的冻结响应，pytest 不再调用 Node。

后续可借鉴的开源项目设计与实施优先级见 [TODO](TODO.md)。

## 使用流程

1. 在「模型设置」分别配置视觉理解和图片生成。填写服务商 Base URL、准确模型 ID 和自己的 SK，然后运行图片能力测试。测试会向服务商发送内置测试图片，可能产生费用。
2. 在首页上传 1–6 张同一商品的 JPEG、PNG 或 WebP 原图，每张不超过 10 MB。服务端检查图片实际内容并生成本机缩略图；仅上传或打开设置页不会向外部服务商发送商品图片。
3. 输入商品资料。点击「AI 帮写」后，原图与文字会发送到所选视觉服务商；页面显示已知事实和待补充项，文案仍可手工编辑。失败不会覆盖原文。
4. 选择模块与平台、市场、语言、质量、比例、风格。页面在生成前显示发送范围与预计调用次数。点击「生成商品详情页」后，任务串行分析原图、规划各模块文案、逐模块生成图片。
5. 在任务页查看阶段、成品图、失败原因；失败模块可单独重试。可取消后续生成、逐张下载、打包 ZIP，或删除任务及其本机资产。「历史任务」可重新打开已保存任务。

首版生图仅实现 OpenAI Images Edits，默认模型 ID 为 `gpt-image-2.5-sunburst`；其他预设只作为视觉输入候选，使用前应通过图片测试确认账户和模型实际能力。Claude 使用原生 Messages 图片块，其余视觉预设使用 OpenAI 兼容 Chat Completions 图片输入。模型 ID 和服务商 API 能力会变化，页面允许编辑准确 ID，不保证每个文本模型都支持视觉输入。旧 GPT Image 1 系列的固定尺寸结果会在本机裁切为选定比例。

SK 只在服务进程内存中，不通过读取接口返回，也不写入浏览器持久存储或任务文件。视觉理解与图片生成分别保存配置；图片生成目前只接受 OpenAI 预设。仅当同一角色的服务商和 Base URL 均不变时，留空 SK 才复用内存中的旧值；更换服务商或 Base URL 后需重填 SK，每次保存都会重置图片能力测试状态。服务重启后需重填与任务创建时相同的服务商、Base URL 和模型 ID；未完成任务进入 `waiting_credentials`，已成功图片仍保留。草稿与任务资产保存在非静态 `data/` 目录；草稿超过 24 小时后在启动或定期清理，任务会保留到用户删除。删除任务会清除任务目录和关联草稿中的原图、成品图及元数据；其他基于同一草稿创建的任务拥有各自的原图副本，仍可查看。模型服务商可能收取图片输入和生成费用，请在自己的账户确认权限与价格。

## 接口与状态

| 接口 | 用途 |
| --- | --- |
| `GET/POST /api/config` | 旧文本模型配置（独立于视觉与生图角色，读取仅返回 `activeProvider`、`profiles` 和 `hasKey`） |
| `POST /api/test`、`POST /api/generate-copy` | 旧文本连接测试 `{reply}` 与基于 `source` 的文案生成 `{text}` |
| `POST /api/drafts`、`GET /api/drafts/:id` | 创建/读取本机图片草稿 |
| `POST /api/drafts/:id/images`、`DELETE /api/drafts/:id/images/:assetId` | 上传/删除草稿图；multipart 字段名 `image` |
| `GET /api/drafts/:id/images/:assetId/preview` | 本机缩略图 |
| `GET/POST /api/roles`、`POST /api/roles/test` | 视觉与生图配置、图片能力测试 |
| `POST /api/assist-copy` | 用草稿原图与文字生成可编辑文案 |
| `POST /api/jobs`、`GET /api/jobs`、`GET /api/jobs/:id` | 创建、列出、读取生成任务 |
| `POST /api/jobs/:id/retry`、`POST /api/jobs/:id/cancel` | 重试失败模块、取消后续生成 |
| `GET /api/jobs/:id/images/:moduleId`、`GET /api/jobs/:id/export` | 单图与 ZIP 下载 |
| `DELETE /api/jobs/:id` | 删除任务和资产 |

接口沿用 camelCase 字段与 `{error, code?}` 错误响应；JSON MIME 为
`application/json; charset=utf-8`，响应禁止缓存。无效 JSON 返回 400，
读取 JSON 的接口请求超过 10000 个 UTF-16 字符返回 413，缺失资源和未知路由返回
404。文本 SK 仅在进程内存中；同一服务商及接口地址下留空 SK 可复用，
更换地址或重启后需重填。

任务状态：`queued → analyzing → planning → generating → completed / partial / failed / canceled`；重启或缺少任务原配置时为 `waiting_credentials`。每个模块独立记录状态、尝试次数、标题、正文、成品图及错误。ZIP 包含 `manifest.json`、`文案与失败清单.txt`，以及 `images/{模块顺序两位数}-{imageFile}`（例如 `images/01-1.png`）。仅打包已完成且文件仍存在的成品图；失败模块的状态、文案与失败原因保留在清单中。原图通过 `GET /api/jobs/:id/originals/:assetId` 读取；原图与成品图均返回对应 MIME，删除后的图片或任务返回 404。任务数据位于 `data/jobs/<UUID>/`，不在 `public/` 中。

FastAPI 的任务读取与操作沿用上述接口和毫秒时间戳。重试请求体为
`{"moduleId":"2"}`，只重置并生成指定的失败模块，保留成功模块的文案、
图片和尝试次数，其他失败模块也保持原样；重复重试已排队模块返回 400。
取消进行中的任务会中止当前可取消请求并阻止后续模型请求，保留已完成结果；
已完成、部分完成或失败任务的取消操作保留原状态，已取消任务不能重试。
删除操作先取消并等待该任务的活动请求和文件写入结束，再删除任务目录及关联
草稿；随后读取返回 404，其他任务各自保存的原图和结果不受影响。

## 验证边界

自动化测试使用本机模拟服务商，已覆盖真实图片字节进入视觉和图片编辑请求、鉴权/能力错误、超限上传、任务持久化、局部失败、重试、取消、ZIP 和删除。没有用户真实 SK 时，不能据此断言真实平台连接已通过。正式使用前，请在模型设置中对自己的视觉与生图模型各运行一次图片测试，再用少量模块核对输出外观、文字和实际费用。

### Python 视觉与文本协议边界

`backend/providers/vision.py` 与 `text.py` 使用注入的 `httpx.AsyncClient`。
OpenAI 兼容协议发送 `/chat/completions`、Bearer 认证与原图 base64 data URL；
Claude 原生协议发送 `/messages`、`x-api-key`、`anthropic-version: 2023-06-01`
与包含原图 base64 的 `image.source`。视觉/文本 Claude 请求分别使用
`max_tokens: 1200/600`，OpenAI 请求使用 `stream: false`。请求超时为 60 秒，
异步任务取消继续向上传递。视觉结果解析 JSON（兼容 Markdown 代码围栏）中的
`facts`、`unknowns`、`text`，非 JSON 文本沿用待核实项回退；内部模型字段为
`response_model`，HTTP 边界负责转为 `responseModel`。服务商错误公开前移除
当前 SK，并保留鉴权、模型、能力、限流、超时、网络与无效响应分类。
协议测试仅使用 mock transport 和虚构凭据。

### Python 图片编辑协议边界

首版 Python 生图适配器 `backend/providers/image.py` 仅支持 OpenAI Images
Edits：以 multipart `image[]` 逐张发送未经转码的原图，包含模型、模块提示词、
尺寸、质量和 `output_format=png`；GPT Image 1 非 mini 模型保留
`input_fidelity=high`。注入的 HTTP 客户端使用 180 秒请求期限，支持任务取消。
返回图片必须通过严格 Base64、JPEG/PNG/WebP 格式和 8000 万像素检查，
由 Pillow 验证结构并完整解码所有帧后输出 PNG；无效或截断的成功响应仍按
`invalid_response` 拒绝。GPT Image 1 的旧固定尺寸结果居中裁切为
4:5（1024×1280）或 16:9（1536×864）。适配器测试使用模拟响应与合成图片，
未调用真实服务商，也不代表用户账户能力已验证。

### Python 任务创建与快照

`POST /api/jobs` 返回 HTTP 201、`{jobId, status: "queued"}`，沿用原有
camelCase 字段、JSON 错误结构和毫秒时间戳。创建前需要至少一张有效草稿原图、
非空商品资料、1–16 个不同的合法模块，以及视觉和生图两个角色的可用配置。
任务保存创建时的资料（最多 4000 字符）、六项设置（各最多 80 字符）、
模块顺序、模型标识与原图/预览副本；`visionProfile` 保存 provider、baseUrl、
model、protocol，`imageProfile` 保存 provider、baseUrl、model。快照不含 SK
或 `apiKey`，凭据仅保存在进程内存中。`create_app(enqueue=callback)` 在任务
持久化后把任务 ID 交给回调；串行执行队列由后续迁移任务接入。

