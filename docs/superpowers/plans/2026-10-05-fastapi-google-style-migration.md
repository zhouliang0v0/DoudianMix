# FastAPI and Google Style Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Python 3.11/FastAPI 独立运行现有商品详情图应用，保留页面、接口和旧数据，并让前后端代码通过适用的 Google 风格检查。

**Architecture:** 开发期间让 FastAPI 使用独立端口和临时数据，按资产、模型、任务、导出顺序迁移。对外保持现有 `/api/*` 与 `data/` 格式；全部契约和模拟模型测试通过后切换原端口，移除 Node 服务端。

**Tech Stack:** Python 3.11、FastAPI、Uvicorn 单 worker、httpx、Pillow、pytest、Pyink、Pylint；页面保留原生 JavaScript/HTML/CSS，前端静态检查可使用 Node 开发工具，运行服务不依赖 Node。

**Spec:** [Superpowers 设计](../specs/2026-10-05-fastapi-google-style-migration-design.md)、[OpenSpec 提案](../../../openspec/changes/migrate-to-fastapi-google-style/proposal.md)、[行为规格](../../../openspec/changes/migrate-to-fastapi-google-style/specs/local-fastapi-runtime/spec.md)、[技术设计](../../../openspec/changes/migrate-to-fastapi-google-style/design.md)、[任务清单](../../../openspec/changes/migrate-to-fastapi-google-style/tasks.md)。

## Global Constraints

- 最终服务使用 Python 3.11，单个 Uvicorn worker，监听 `127.0.0.1:8765`；Python 3.9 必须被项目版本约束拒绝。开发阶段使用独立端口和临时数据目录。
- 页面保持原生 HTML、CSS、JavaScript；现有 `/api/*` 路径、camelCase 字段、成功状态码、`{error, code?}` 结构与图片/ZIP 类型保持兼容。
- `data/drafts/<uuid>/`、`data/jobs/<uuid>/` 的目录、JSON 字段、毫秒时间戳及任务状态保持可读；SK 只在服务进程内存中，不写入响应、任务文件、日志或 ZIP。
- 同一草稿最多 6 张 JPEG/PNG/WebP，每张最多 10 MB；只在用户触发 AI 帮写或生成时向服务商发送商品图，模型能力测试使用内置图。
- 全局生成并发为 1；重启后的未完成任务进入 `waiting_credentials`，成功模块保留，单模块重试只重做指定失败模块。
- 生图继续只承诺 OpenAI Images Edits；现有 13 个服务商预设中的视觉能力仍需逐模型测试，不扩大模型能力范围。
- Python 遵循 Google Python Style Guide；JavaScript、HTML、CSS 遵循对应 Google 指南。Google JavaScript 指南虽停止更新，本次按用户选择仍保留原生 JS。
- 生产 `data/` 切换前备份；Node 与 FastAPI 不同时写该目录。真实账户 SK/服务商调用不属于本迁移的自动化通过条件。

## Review Focus

- Host 为 `localhost.evil` 或 Origin 带不同端口时必须返回 403；Task 1 的 `test_rejects_lookalike_host_and_foreign_origin` 固定此行为。
- multipart 含多个文件、错误字段或分块超限时必须拒绝并清理临时资产；Task 3 的 `test_rejects_malformed_multipart_without_leaks` 固定此行为。
- 旧任务 JSON 含额外合法字段时，读取和状态更新不能删除该字段；Task 4 的 `test_preserves_legacy_extra_fields` 固定此行为。
- 服务商错误正文碰巧包含 SK 时，公开错误与日志不能回显密钥；Task 6 的 `test_redacts_provider_secret` 固定此行为。
- 两个任务同时入队仍只能有一个模型调用在运行；Task 10 的 `test_runner_serializes_two_jobs` 固定此行为。

---

## File Map

| 路径 | 唯一职责 |
| --- | --- |
| `pyproject.toml`、`.python-version`、`uv.lock`、`.gitignore`、`config/google.pylintrc` | Python 3.11、锁定依赖与 Google Python 检查；`uv.lock` 由 uv 生成并提交。 |
| `backend/main.py`、`backend/errors.py` | 应用工厂/生命周期、来源与静态路由限制、统一错误映射。 |
| `backend/api/drafts.py`、`models.py`、`jobs.py`、`legacy.py` | HTTP 边界与旧接口兼容，不直接实现文件格式或服务商协议。 |
| `backend/asset_store.py`、`backend/job_store.py` | 草稿和任务文件存储、内容校验、原子更新及旧数据恢复。 |
| `backend/profile_store.py`、`backend/provider_catalog.py`、`public/providers.json` | 内存 SK/角色配置和单份非秘密预设数据。 |
| `backend/providers/common.py`、`vision.py`、`text.py`、`image.py` | HTTP 协议适配、错误分类、图片输入与成品图解析。 |
| `backend/job_runner.py`、`backend/prompt_builder.py`、`backend/exporter.py` | 串行生成、提示词、取消/重试、ZIP 结构。 |
| `public/`、`eslint.config.mjs`、`stylelint.config.cjs`、`.htmlvalidate.json` | 三页行为、原生 JS/CSS/HTML 风格检查。 |
| `tests/test_*.py`、`tests/conftest.py`、`tests/fixtures/legacy_data/` | pytest 契约、旧数据、模拟模型与端到端验证；旧 Node 测试保留至切换通过。 |
| `scripts/check.ps1`、`docs/google-style-audit.md`、`README.md`、`docs/system-design.md`、`docs/verification.md` | 一键检查、人工规则审查、启动与架构/验收记录。 |

## Execution Rules

每项任务先读设计与对应 OpenSpec 条目，按下列步骤红灯→绿灯→完整相关测试执行；每次提交只包含该任务的文件。执行时先按 `superpowers:using-git-worktrees` 建立隔离工作区，完成任务时再勾选对应 OpenSpec checkbox。实施中遇到行为歧义，以现有 Node 响应和已批准的行为规格为准；改变外部契约前回到设计审阅。

### Task 1: Python 3.11 环境、应用入口与安全边界（OpenSpec 1.1–1.2）

**Files:** Create `pyproject.toml`, `.python-version`, `uv.lock`, `config/google.pylintrc`, `backend/__init__.py`, `backend/main.py`, `backend/errors.py`, `tests/conftest.py`, `tests/test_app.py`; modify `.gitignore`, `README.md`.

**Interfaces:** Produces `create_app(data_dir: pathlib.Path | None = None, transport: httpx.AsyncBaseTransport | None = None) -> fastapi.FastAPI` and `app = create_app()` in `backend/main.py`, plus `AppError(message: str, status_code: int = 400, code: str | None = None)` in `backend/errors.py`. Later tasks extend this app through routers and injected stores. Static allowlist initially includes `/`, `/index.html`, `/settings.html`, `/results.html`, `/styles.css`, `/script.js`, `/settings.js`, `/results.js`, `/providers.js`.

- [x] **Step 1: 准备解释器与依赖。** 运行 `uv python install 3.11`；建立 `.python-version` 为 `3.11`、`pyproject.toml` 的 `requires-python = ">=3.11,<3.12"` 和 `[tool.pyink] line-length = 80`，声明 FastAPI、Uvicorn、httpx、python-multipart、Pillow 及 pytest/Pyink/Pylint 开发依赖，固定官方 Google Pylint 配置到 `config/google.pylintrc`，运行 `uv lock` 和 `uv sync --python 3.11`。`uv run --locked python --version` 显示 3.11.x；`uv sync --python 3.9` 拒绝版本。
- [x] **Step 2: 写失败测试。** `test_local_pages_and_private_path`: `assert client.get("/").status_code == 200`、`assert client.get("/data/jobs/x/job.json").status_code == 404`；`test_rejects_lookalike_host_and_foreign_origin`: `assert foreign.status_code == 403`；`test_python_requirement`: `assert project["requires-python"] == ">=3.11,<3.12"`。
- [x] **Step 3: 运行红灯。** `uv run --locked pytest tests/test_app.py -q` 应因缺少 `backend.main.create_app` 或安全路由而失败。
- [x] **Step 4: 实现并验证。** `create_app()` 绑定静态白名单、Host/Origin 校验与 `{error, code?}` 错误处理；用 `TestClient(app, base_url="http://127.0.0.1:8766")` 注入测试数据目录。运行 `uv run --locked pytest tests/test_app.py -q`、`uv run --locked pyink --check backend tests`、`uv run --locked pylint --rcfile=config/google.pylintrc backend` 与 `npm test`，预期均通过且 Node 基线仍 15 项通过；README 写开发端口与 Python 3.11 命令。
- [x] **Step 5: 提交。** 提交本任务文件，提交信息 `feat: add Python 3.11 FastAPI shell`。

### Task 2: 旧草稿格式与图片资产（OpenSpec 2.1）

**Files:** Create `backend/asset_store.py`, `tests/test_assets.py`, `tests/fixtures/legacy_data/drafts/`; modify `README.md`.

**Interfaces:** Produces `AssetStore(data_dir: pathlib.Path)` with `create_draft() -> dict`, `read_draft(draft_id: str) -> dict`, `add_image(draft_id: str, content: bytes, filename: str) -> dict`, `remove_image(draft_id: str, asset_id: str) -> None`, `read_image(draft_id: str, asset_id: str, preview: bool = False) -> tuple[bytes, str]`, `copy_to_job(draft_id: str, job_id: str) -> list[dict]`, `cleanup_expired(now_ms: int) -> int`, `remove_draft(draft_id: str) -> None`.

- [x] **Step 1: 写失败测试。** `test_reads_legacy_draft_and_preserves_original_bytes`: `assert store.read_image(draft_id, asset_id)[0] == original_bytes`；`test_rejects_fake_truncated_oversize_and_too_many_images`: 各无效输入抛 `AppError`，`assert len(store.read_draft(draft_id)["images"]) <= 6`，失败后目录没有新资产。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_assets.py -q` 应因缺少 `AssetStore` 失败。
- [x] **Step 3: 实现。** `AssetStore` 使用 UUID 路径校验、Pillow 完整解码/EXIF 转正/400 像素 WebP 缩略图；草稿 JSON 和资产文件先临时写入再提交，24 小时清理只删除到期草稿。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_assets.py -q` 通过；README 的格式、限制和只本地存储描述与实现一致。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port draft asset storage`。

### Task 3: 草稿 HTTP 路由（OpenSpec 2.2）

**Files:** Create `backend/api/__init__.py`, `backend/api/drafts.py`, `tests/test_drafts_api.py`; modify `backend/main.py`.

**Interfaces:** Consumes `AssetStore` from Task 2. Produces `router: fastapi.APIRouter` covering `POST /api/drafts` (201), `GET /api/drafts/{draft_id}`, `POST /api/drafts/{draft_id}/images` (201, multipart key `image`), `DELETE /api/drafts/{draft_id}/images/{asset_id}` and `GET .../preview`.

- [x] **Step 1: 写失败测试。** `test_draft_api_matches_node_contract`: `assert upload.status_code == 201`、`assert upload.json()["assetId"]`、`assert preview.headers["content-type"] == "image/webp"`、`assert deleted.json() == {"ok": True}`；`test_rejects_malformed_multipart_without_leaks`: 错误字段、多文件、分块超限均拒绝，`assert not leaked_files`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_drafts_api.py -q` 应因草稿路由 404 失败。
- [x] **Step 3: 实现。** 用 `UploadFile` 限制单个 `image` 字段与读取字节数，调用 `AssetStore`；文件响应带正确 MIME 和 no-store 缓存头，将异常映射到旧状态码。
- [x] **Step 4: 验证。** `uv run --locked pytest tests/test_drafts_api.py tests/test_assets.py -q` 全通过；记录一组 Node/FastAPI 响应对照到测试夹具注释。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port draft API`。

### Task 4: 任务文件、旧数据恢复与原子更新（OpenSpec 2.3）

**Files:** Create `backend/job_store.py`, `tests/test_job_store.py`, `tests/fixtures/legacy_data/jobs/`; modify `docs/system-design.md` 的迁移说明。

**Interfaces:** Produces `JobStore(data_dir: pathlib.Path)` with `create(job: dict) -> dict`, `read(job_id: str) -> dict`, `update(job_id: str, mutate: collections.abc.Callable[[dict], None]) -> dict`, `list_jobs() -> list[dict]`, `recover() -> None`, `remove(job_id: str) -> None`, `job_dir(job_id: str) -> pathlib.Path`. `update()` 对同一任务加锁，并通过临时文件和 `os.replace` 写入。

- [x] **Step 1: 写失败测试。** `test_reads_legacy_job_and_waits_for_credentials`: `assert store.read(job_id)["status"] == "waiting_credentials"` 且成功图存在；`test_preserves_legacy_extra_fields`: `assert store.read(job_id)["legacyExtra"] == "keep"`；`test_atomic_update_survives_interruption`: 模拟替换前异常，`assert json.loads(job_path.read_text())["id"] == job_id`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_job_store.py -q` 应因缺少 `JobStore` 失败。
- [x] **Step 3: 实现。** 保留毫秒时间戳、camelCase、任务与模块状态值；恢复 `queued/analyzing/planning/generating` 为 `waiting_credentials`，运行中模块为 `pending`；Windows 文件占用时有限重试。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_job_store.py -q` 通过；系统设计文档注明 Python 存储层的旧格式兼容而非声称已经切换生产服务。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port persistent job storage`。

### Task 5: 单份预设、内存角色与配置 API（OpenSpec 3.1）

**Files:** Create `public/providers.json`, `backend/provider_catalog.py`, `backend/profile_store.py`, `backend/api/models.py`, `tests/test_profiles.py`; modify `backend/main.py`, `README.md`.

**Interfaces:** Produces `load_catalog(path: pathlib.Path) -> list[dict]`, `ProfileStore(catalog: list[dict])`, `save_role(body: dict) -> dict`, `get_role(role: str) -> dict`, `public_roles() -> dict`, `mark_tested(role: str) -> None`, plus `GET/POST /api/roles`. `get_role()` returns in-memory SK only to server code; public responses expose `hasKey`, never `apiKey`.

- [x] **Step 1: 写失败测试。** `test_role_configs_are_separate_and_secret_is_not_public`: `assert "apiKey" not in json.dumps(store.public_roles())` 且两角色 SK 不同；`test_provider_catalog_matches_existing_options`: `assert len(catalog) == 13`、顺序等于旧列表，image 角色只接受 `openai`；非法 URL 和切换 URL 后留空 SK 抛 `AppError`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_profiles.py -q` 应因配置路由或存储缺失失败。
- [x] **Step 3: 实现。** 把 `providers.js` 的非秘密字段迁为 `public/providers.json` 单一来源，在静态白名单加入 `/providers.json`；Python 加载同一文件，保留 HTTPS/本机 HTTP 校验和内存 SK 复用条件；在应用工厂注册配置路由。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_profiles.py -q` 通过；README 保留角色、服务商与 SK 重填说明。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port in-memory model profiles`。

### Task 6: 视觉、文本与服务商错误边界（OpenSpec 3.2）

**Files:** Create `backend/providers/__init__.py`, `backend/providers/common.py`, `backend/providers/vision.py`, `backend/providers/text.py`, `tests/test_vision_providers.py`; modify `backend/errors.py`.

**Interfaces:** Produces `analyze_vision(profile: dict, images: list[tuple[bytes, str]], brief: str, settings: dict, client: httpx.AsyncClient) -> dict` with `facts`, `unknowns`, `text`, `response_model` (HTTP 边界转为 `responseModel`); `call_text(profile: dict, prompt: str, client: httpx.AsyncClient) -> str`; `classify_provider_error(status: int, message: str, secret: str) -> AppError`. Request timeout is 60 秒。

- [x] **Step 1: 写失败测试。** `test_openai_and_claude_send_actual_image_bytes`: `assert decoded_image == original_bytes` 并检查认证头；`test_redacts_provider_secret`: `assert sk not in str(error)`、`assert sk not in caplog.text`；`test_classifies_auth_model_rate_limit_timeout`: 401/404/429/504 分别映射 `auth/model/rate_limit/timeout`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_vision_providers.py -q` 应因适配函数缺失失败。
- [x] **Step 3: 实现。** 用注入的 `httpx.AsyncClient` 保留旧请求协议、视觉 JSON/文本解析、60 秒超时和取消；公开错误码保持 `auth/model/capability/rate_limit/timeout/network/invalid_response` 语义，内部使用 `response_model`。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_vision_providers.py -q` 通过；README 记录 OpenAI 兼容与 Claude 视觉协议边界。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port vision and text adapters`。

### Task 7: 图片编辑适配器（OpenSpec 3.3）

**Files:** Create `backend/providers/image.py`, `tests/test_image_provider.py`; modify `backend/providers/common.py`, `README.md`.

**Interfaces:** Produces `GeneratedImage(data: bytes, mime_type: str, response_model: str)`, `resolve_size(ratio: str, model: str) -> str`, `resolve_quality(quality: str) -> str`, `generate_image(profile: dict, references: list[tuple[bytes, str]], module_brief: str, ratio: str, quality: str, client: httpx.AsyncClient) -> GeneratedImage`. Timeout 180 秒；结果统一为 PNG。

- [x] **Step 1: 写失败测试。** `test_edits_request_contains_each_reference_and_settings`: `assert len(sent_images) == len(references)` 并检查模型、提示词、尺寸、质量；`test_rejects_truncated_success_response`: 截断像素的 200 响应抛出 `code == "invalid_response"`；`test_legacy_ratio_is_cropped`: `assert output.size == (1024, 1280)` 或 `(1536, 864)`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_image_provider.py -q` 应因 `generate_image` 缺失失败。
- [x] **Step 3: 实现。** multipart 请求沿用 `/images/edits`、`image[]`、`output_format=png`；Base64 解码后由 Pillow 完整读取、检查格式/像素并转 PNG，旧模型固定尺寸按现有规则裁切。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_image_provider.py -q` 通过；README 明确首版仅支持 OpenAI Images Edits 生图。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port image edits adapter`。

### Task 8: 模型能力测试与 AI 帮写路由（OpenSpec 3.3、现有文案行为）

**Files:** Modify `backend/api/models.py`, `backend/main.py`; create `backend/prompt_builder.py`, `tests/test_model_api.py`.

**Interfaces:** Consumes `ProfileStore.get_role()`, `analyze_vision()`, `generate_image()`, `AssetStore.read_image()`。Produces `POST /api/roles/test` 返回 `{ok, role, responseModel}`，`POST /api/assist-copy` 返回 `{text, facts, unknowns, provider, responseModel, imageCount}`；`ensure_unknown_markers(analysis: dict) -> str` in `backend/prompt_builder.py`.

- [x] **Step 1: 写失败测试。** `test_role_test_uses_built_in_png_and_marks_only_success`: `assert sent_image != product_image`、`assert roles["vision"]["tested"] is False`（失败时）；`test_assist_copy_sends_draft_images_and_returns_no_replacement_on_error`: `assert reply["imageCount"] == 2`，失败响应没有替换文案；缺图/未配置角色返回旧式错误。浏览器中原文保留由 Task 14 验证。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_model_api.py -q` 应因测试或帮写路由 404 失败。
- [x] **Step 3: 实现。** 设置页测试图用 Pillow 创建，按角色调用适配器；帮写读取草稿图片并按旧字段返回结果，未知事实添加“待补充”标记；路由只从进程内 `ProfileStore` 取得 SK。
- [x] **Step 4: 验证。** `uv run --locked pytest tests/test_model_api.py tests/test_profiles.py tests/test_vision_providers.py -q` 通过。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port image capability test and copy assist`。

### Task 9: 任务创建与输入快照（OpenSpec 4.1）

**Files:** Create `backend/api/jobs.py`, `tests/test_job_creation.py`; modify `backend/main.py`, `backend/job_store.py`, `backend/asset_store.py`, `README.md`.

**Interfaces:** Produces `create_job(payload: dict, assets: AssetStore, jobs: JobStore, profiles: ProfileStore) -> dict` in `backend/api/jobs.py` and `POST /api/jobs` returning 201 `{jobId, status}`. Task 10 consumes a created `queued` job and adds queue execution. Module names/order equal current 16-name list from `server/routes/jobs.js`.

- [x] **Step 1: 写失败测试。** `test_creates_snapshot_without_secret`: `assert response.status_code == 201`、`assert "apiKey" not in job_path.read_text()`、`assert job["modules"][0]["name"] == selected[0]`；`test_rejects_empty_brief_duplicate_or_seventeen_modules`: `assert response.status_code == 400` 且任务目录未创建；`test_legacy_job_shape_matches_node` 比较关键字段和毫秒时间戳。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_job_creation.py -q` 应因任务路由 404 失败。
- [x] **Step 3: 实现。** 复用 `AssetStore.copy_to_job()`、`JobStore.create()`，校验 1–16 个不同合法模块、非空资料和两个角色配置；创建任务后只交给可注入入队回调，队列实现留给 Task 10。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_job_creation.py -q` 通过；README 记录 201 响应和快照字段不含 SK。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port job creation contract`。

### Task 10: 串行生成、模块规划与重启续跑（OpenSpec 4.2）

**Files:** Create `backend/job_runner.py`, `tests/test_job_runner.py`; modify `backend/main.py`, `backend/api/jobs.py`, `backend/api/models.py`, `backend/prompt_builder.py`, `docs/system-design.md`.

**Interfaces:** Produces `JobRunner(jobs: JobStore, assets: AssetStore, profiles: ProfileStore, client: httpx.AsyncClient)`, async `start() -> None`, `close() -> None`, `enqueue(job_id: str) -> None`, `resume_waiting() -> None`, `wait_idle(job_id: str) -> None`, `cancel(job_id: str) -> None`. `run_job(job_id: str) -> None` performs one analysis and each pending module's text/image calls. `build_module_brief(module_name: str, brief: str, analysis: dict, settings: dict) -> str` and `module_ratio(name: str, requested: str) -> str` preserve current rules.

- [x] **Step 1: 写失败测试。** `test_two_images_three_modules_partial_failure_and_resume`: `assert job["status"] == "partial"`、`assert completed_image.exists()`，恢复后成功模块 `attempts` 不变；`test_runner_serializes_two_jobs`: `assert max_active == 1`；`test_output_is_saved_before_completed_status`: `assert image_path.exists()` 当模块状态为 `completed`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_job_runner.py -q` 应因 `JobRunner` 缺失失败。
- [x] **Step 3: 实现。** FastAPI lifespan 管理单消费者队列、草稿定时清理和退出；任务阶段与旧状态机一致，保存每模块文案/图片、失败码与进度，保存分析时把内部 `response_model` 转为现有 JSON 字段；保存角色配置后尝试 `resume_waiting()`；阻塞文件操作放到线程，避免阻塞模型 HTTP 等待。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_job_runner.py tests/test_job_creation.py tests/test_job_store.py -q` 通过；系统设计文档更新 Python 队列与生命周期段落，但标注生产尚未切换。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port serial generation runner`。

### Task 11: 任务读取、取消、重试与删除（OpenSpec 4.3）

**Files:** Modify `backend/api/jobs.py`, `backend/job_runner.py`; create `tests/test_job_actions.py`; modify `README.md`.

**Interfaces:** Produces `GET /api/jobs`, `GET/DELETE /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/cancel`, `POST /api/jobs/{job_id}/retry`。`retry` 仅接受 body `moduleId` 指向 `failed` 模块；删除先取消并等待活动任务，再移除任务与关联草稿。

- [x] **Step 1: 写失败测试。** `test_retry_only_target_failed_module`: `assert calls[successful_id] == 1`、`assert calls[other_failed_id] == 1`；`test_cancel_stops_next_module_and_preserves_result`: `assert calls[next_id] == 0` 且成功图可读；`test_delete_waits_and_removes_linked_assets`: 删除后读取为 404，`assert other_job_exists`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_job_actions.py -q` 应因操作路由 404 失败。
- [x] **Step 3: 实现。** 通过 `JobStore.update()` 修改状态并调用 `JobRunner` 取消/入队；重试只将指定失败模块改为 pending，防止取消与重试的重复调用。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_job_actions.py tests/test_job_runner.py -q` 通过；README 说明重试、取消、删除与结果保留行为。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port job lifecycle actions`。

### Task 12: 原图、成品图和 ZIP 导出（OpenSpec 4.4）

**Files:** Create `backend/exporter.py`, `tests/test_results_api.py`; modify `backend/api/jobs.py`, `README.md`.

**Interfaces:** Produces `build_job_archive(job: dict, job_dir: pathlib.Path) -> bytes` and `GET /api/jobs/{job_id}/originals/{asset_id}`, `GET /api/jobs/{job_id}/images/{module_id}`, `GET /api/jobs/{job_id}/export`。ZIP 包含 `manifest.json`、`文案与失败清单.txt` 和 `images/{order:02d}-{imageFile}`。

- [x] **Step 1: 写失败测试。** `test_zip_contains_manifest_copy_and_completed_images`: `assert "manifest.json" in archive.namelist()`、`assert failed_image_name not in archive.namelist()`；`test_image_mime_and_deleted_result_404`: `assert image.headers["content-type"] == "image/png"`、删除后 404；`test_legacy_metadata_cannot_escape_job_dir`: `assert traversal_response.status_code == 404`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_results_api.py -q` 应因结果路由 404 失败。
- [x] **Step 3: 实现。** 使用标准库 `zipfile` 写与旧版一致的 manifest/文案文件；读取结果前将文件名约束在任务目录内，并用记录中的模块/图片 ID 关联，不接受任意路径。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_results_api.py -q` 通过；README 的下载结构与实际 ZIP 一致。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: port result delivery and ZIP export`。

### Task 13: 旧文本接口与完整 HTTP 契约（OpenSpec 5.1）

**Files:** Create `backend/api/legacy.py`, `tests/test_contract.py`; modify `backend/main.py`, `backend/api/models.py`, `backend/errors.py`, `README.md`.

**Interfaces:** Produces `GET/POST /api/config`, `POST /api/test`, `POST /api/generate-copy` and central FastAPI validation/error handlers. All existing endpoints use `{error, code?}` on failure; request model aliases preserve camelCase.

- [x] **Step 1: 写失败测试。** `test_legacy_text_api_matches_node`: `assert python_reply.status_code == node_reply.status_code`、公开字段集合一致；`test_all_api_error_shapes_are_page_readable`: `assert "error" in reply.json()`、`assert "detail" not in reply.json()`；`test_contract_status_and_content_types`: 成功状态与 MIME 对照 Node。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_contract.py -q` 应因旧接口 404 或错误形状不同失败。
- [x] **Step 3: 实现。** 旧文本 API 使用 `ProfileStore` 与 `call_text()`；统一映射 `AppError`、Pydantic 校验、请求体过长、文件缺失与未知路由，保留旧页面可显示的错误文本类别。
- [x] **Step 4: 验证与文档。** `uv run --locked pytest tests/test_contract.py tests/test_model_api.py -q` 通过；README 接口表与实际路由一致。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `feat: preserve legacy HTTP contract`。

### Task 14: 原生页面与 Google 前端规范（OpenSpec 5.3）

**Files:** Modify `public/index.html`, `public/settings.html`, `public/results.html`, `public/script.js`, `public/settings.js`, `public/results.js`, `public/styles.css`, `package.json`, `package-lock.json`; remove `providers.js` after consumers migrate; create `eslint.config.mjs`, `stylelint.config.cjs`, `.htmlvalidate.json`, `docs/google-style-audit.md`.

**Interfaces:** Consumes `public/providers.json` from Task 5 and existing `/api/*` fields。设置页加载同一非秘密预设数据；首页和结果页操作、DOM ID、导航地址与现有页面保持一致。前端仅使用原生 JS，无 TypeScript 构建。

- [x] **Step 1: 写失败检查。** 将 ESLint、Stylelint、HTML 校验器加入 `package.json` 开发依赖与 `lint` 脚本并更新 lockfile；配置检查规则，对现有压缩 JS/CSS/HTML 运行检查，记录实际规则失败；准备三页浏览器检查清单（上传、设置、AI 帮写、轮询、重试、下载、390px 与桌面布局）。
- [x] **Step 2: 运行红灯。** `npm run lint` 应因旧代码风格违规失败；浏览器基线截图保留供对照。
- [x] **Step 3: 实现。** 按 Google 对应指南重排代码、处理真实规则违规并让设置页读取共享预设；保留所有已用 DOM ID 和可见文案。`docs/google-style-audit.md` 逐条记录自动检查覆盖、人工检查及有理由的少量例外。
- [x] **Step 4: 验证。** `npm run lint` 通过；三页浏览器清单全部通过，截图对照无关键布局退化；`uv run --locked pytest tests/test_contract.py -q` 仍通过。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `style: align native frontend with Google guides`。

### Task 15: 统一质量门与模拟端到端（OpenSpec 5.2、6.1）

**Files:** Create `scripts/check.ps1`, `tests/test_e2e.py`; modify `pyproject.toml`, `README.md`, `docs/verification.md`, `docs/google-style-audit.md`.

**Interfaces:** `scripts/check.ps1` 依次运行 `uv run --locked pyink --check backend tests`, `uv run --locked pylint --rcfile=config/google.pylintrc backend`, `uv run --locked pytest -q`, `npm run lint`；任一步失败则非零退出。`tests/test_e2e.py` 只调用本机模拟视觉/生图服务，不需要真实 SK。

- [x] **Step 1: 写失败测试。** `test_two_images_three_modules_retry_zip_delete`: `assert job["status"] == "partial"`、重试后 `assert job["status"] == "completed"`、`assert "manifest.json" in archive.namelist()`、删除后读取 404；`test_check_script_propagates_failure`: 模拟检查失败时 `assert process.returncode != 0`。
- [x] **Step 2: 运行红灯。** `uv run --locked pytest tests/test_e2e.py -q` 应因模拟端到端夹具或统一检查入口缺失失败。
- [x] **Step 3: 实现。** 加入本机模拟服务商夹具和统一检查脚本；使用 Task 1 固定的 Google Pylint 配置，修正检查发现的实际违规而不整体关闭规则。
- [x] **Step 4: 验证与文档。** `powershell -File scripts/check.ps1` 通过；README 写一条复现命令，`docs/verification.md` 记录模拟结果及真实 SK 尚未验证的边界。
- [x] **Step 5: 提交。** 提交本任务文件，信息 `test: add migration quality gate and end-to-end check`。

### Task 16: 生产数据切换与 Node 服务端清理（OpenSpec 6.2–7.1）

**Files:** Remove `server.js`, `server/`, `smoke-test.js`, `tests/*.test.js` and Node 服务端依赖；modify `package.json`, `README.md`, `docs/system-design.md`, `docs/verification.md`, `.gitignore`, `openspec/changes/migrate-to-fastapi-google-style/tasks.md`.

**Interfaces:** 最终启动 `uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8765 --workers 1`；前端 lint 可继续使用 `npm run lint` 作为开发命令，生产运行只需 Python 3.11。旧数据恢复通过后才移除 Node 代码。

- [x] **Step 1: 写切换验收清单。** 指定已加入 `.gitignore` 的 `backups/fastapi-cutover/<时间戳>/data/` 为备份位置，记录当前 Node 进程、旧草稿/已完成任务/未完成任务的匿名 ID；写明失败时先停止 FastAPI、再恢复备份和原 Node 提交的回退顺序。不得把实际 SK 或商品图放进 Git。
- [x] **Step 2: 独立端口绿灯。** 在 FastAPI 临时目录执行 `powershell -File scripts/check.ps1` 和三页浏览器流程，结果均通过后停止 Node；生产 `data/` 此前始终由 Node 独占写入。
- [x] **Step 3: 停机备份。** 确认 Node 已退出并且无进程写生产 `data/` 后，再复制到 Step 1 指定的备份位置；记录文件数量和哈希并验证副本完整。
- [x] **Step 4: 切换并验证。** 在原端口启动 Python 3.11 服务，读取旧草稿/任务、下载已完成图/ZIP、确认未完成任务为 `waiting_credentials` 且成功图未丢；若任一失败，执行 Step 1 回退并停止清理。
- [x] **Step 5: 清理并最终检查。** 切换成功后移除 Node 服务端/其依赖和旧测试，更新 README、系统设计与验证记录；运行 `powershell -File scripts/check.ps1`、`openspec validate migrate-to-fastapi-google-style --strict`、干净环境启动冒烟，预期全部通过且 `npm start` 不再是运行入口。
- [x] **Step 6: 提交。** 提交清理、文档、OpenSpec 任务勾选及验收记录，信息 `chore: complete Python-only FastAPI cutover`。

## Plan Self-Review

- OpenSpec `local-fastapi-runtime` 的启动、接口、旧数据、安全和任务行为分别由 Task 1、3/8/9/11/12/13、2/4、1/5/6/8、9/10/11/12 验证；真实 SK 验证继续留在原变更。
- Review Focus 五项分别在 Task 1、3、4、6、10 的具名测试固定；任务步骤均有红灯、实现、绿灯和提交。
- 生产切换与 Node 清理集中在 Task 16，且以 Task 15 完整模拟验收通过为前置条件；每项任务的接口名与上游提供者一致。
