# Design

## Context

迁移动机见 [proposal.md](proposal.md)，完整目标与验收边界见 [Superpowers 设计](../../../docs/superpowers/specs/2026-10-05-fastapi-google-style-migration-design.md)。当前 Node 服务由 `server.js`、`server/app.js`、`server/routes/`、`server/services/` 和 `server/providers/` 组成；`public/` 的三页调用同源 `/api/*`；`data/` 保存草稿、任务 JSON 和图片。现有 15 项 Node 测试全部通过，适合作为行为对照。当前机器命令行只有 Python 3.9，正式目标固定为 Python 3.11。

## Goals / Non-Goals

**Goals:**

- 使 Python 3.11/FastAPI 版本在独立端口通过契约、旧数据和端到端验收后，独立接管 `127.0.0.1:8765`。
- 用清晰的路由、业务服务、数据存储和服务商适配边界替换现有 Node 模块，保留外部行为与本机安全约束。
- 把适用的 Google Python、JavaScript、HTML/CSS 规则变成可重复的检查与审查清单。

**Non-Goals:**

- 引入数据库、Redis、多进程任务执行、用户账户、远程访问或 TypeScript 工具链。
- 新增模型服务商能力；真实账户 SK 验证仍属于原有变更的独立验收。

## Decisions

### 1. 单进程 FastAPI 服务与分层接口

使用 `backend/main.py` 的应用工厂组合 `backend/api/` 路由、`backend/services/` 业务服务、`backend/providers/` 协议适配和 `backend/schemas/` 边界模型。应用生命周期完成旧任务恢复、草稿清理调度、队列启动和退出清理。Uvicorn 固定一个 worker，保持内存 SK 与全局串行队列语义。页面资产仍按白名单提供，服务绑定 loopback 并验证 Host/Origin。

备选为一次性单文件重写或长期保留 Node/FastAPI 双服务。前者难以独立验证状态机，后者需要维护两套后端；分层且逐模块迁移更符合既有测试与数据边界。[FastAPI 的 APIRouter](https://fastapi.tiangolo.com/tutorial/bigger-applications/) 和 [lifespan](https://fastapi.tiangolo.com/advanced/events/) 支持这些边界。

### 2. 保留现有磁盘格式与 HTTP 形状

Python 存储层直接读取现有 `draft.json`、`job.json`、原图、缩略图和结果图；路径由严格校验后的 UUID 构造。时间戳继续为毫秒，JSON 字段及任务状态值不改名。写入先保存临时文件再替换目标，并对同一任务串行更新；Windows 文件占用导致的替换失败需有限重试。HTTP 边界将 Python 内部 `snake_case` 映射为现有 camelCase；统一异常处理把输入校验、缺失资源和服务商错误映射到当前页面可识别的状态码与 `{error, code?}`，避免 FastAPI 默认错误格式改变页面行为。

备选为为旧数据做一次性转换或直接更换 API 版本。两者会扩大切换风险，本次无需改变用户数据结构。开发测试使用临时数据目录，不让两个后端同时写生产 `data/`。

### 3. 模型请求、图片与任务执行

使用可注入的 HTTP 客户端实现现有 OpenAI 兼容视觉、Claude Messages、OpenAI Images Edits 与文本请求，以保留请求体、图片字节、认证头和错误分类。图片处理使用 Pillow 完整解码、格式和像素限制、EXIF 方向处理及缩略图/结果图生成。生成使用应用内单消费者队列；每次状态变化和成功图片都持久化，取消停止后续模块并中止当前可取消请求。进程重启时恢复为 `waiting_credentials`，配置恢复后仅处理待执行或重试模块。

备选为立即引入外部任务队列。当前单用户本机系统需要与进程内 SK 协同，外部队列增加运行依赖与密钥传递复杂度，暂不采用。FastAPI 的 `BackgroundTasks` 也不代替现有可恢复任务状态机。

### 4. 前端预设与 Google 风格检查

现有服务商列表从 Node 可执行的 `providers.js` 转为单份非秘密数据文件，由 Python 读取并供页面使用；设置页保留原有选项、角色限制和可编辑模型 ID。三页继续使用原生 JavaScript，整理为可审查的多行代码，保留 UI 与交互。

Python 以 [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) 和其 Pylint 配置为依据，运行 Pyink 格式检查、Pylint 和 pytest。前端以 [Google JavaScript](https://google.github.io/styleguide/jsguide.html)、[HTML/CSS](https://google.github.io/styleguide/htmlcssguide.html) 指南为依据，运行对应静态检查并人工核查无法自动判定的条款。例外必须逐条注明规则和理由，不允许大范围关闭检查。项目提供可复现的统一检查命令。

## Risks / Trade-offs

- [旧 JSON 的隐含字段或排序差异] → 从现有测试制作旧数据样本，对照 Node/FastAPI 的关键响应，保留未知但合法的任务字段。
- [Python 图片库与 Sharp 的容错差异] → 用有效、伪装、截断、超限和 EXIF 图片做内容测试；对结果图完整解码后再展示。
- [任务取消与文件写入竞态] → 每任务更新串行化，成功图片先落盘再写状态，测试取消、重试和删除相邻时序。
- [Windows 文件替换与生产数据切换] → 使用有限重试、切换前备份、切换时只保留一个写入进程；回退时先停止新服务。
- [JS/CSS 格式整理引起交互或布局变化] → 保留 DOM 和 API 行为，验收三页的关键流程与响应式布局。
- [真实服务商接口变化] → 使用本机模拟端到端测试作为迁移完成条件，真实账户连接另行验证并记录差异。

## Migration Plan

1. 固定当前 15 项测试和旧数据样本，建立 Python 3.11 开发环境、基础服务及风格检查入口。
2. 迁移图片资产与 JSON 存储，再迁移模型配置/适配器、任务队列/导出和兼容接口；每块先写失败测试，再实现并对照现有 Node 行为。期间 FastAPI 只在独立端口使用临时数据。
3. 整理前端 JS/HTML/CSS 和共享服务商预设，使用 FastAPI 完成首页、设置页、结果页的模拟服务商端到端验收。
4. 停止 Node 后备份静止的生产 `data/`，使用 Python 3.11 读取旧草稿和任务，在原端口启动 FastAPI，验证等待凭据恢复、成功图片和下载。
5. 验收通过后移除 Node 服务端、Node 服务端依赖及旧启动命令，更新 README、系统设计与验证记录。若切换验收失败，先停止 FastAPI，再用备份数据和原 Node 提交恢复服务；记录差异后继续在独立端口修正。
