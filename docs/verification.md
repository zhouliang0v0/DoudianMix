# 本机验收记录

当前运行时为 Python 3.11 / FastAPI，生产命令见 README。完整 Python 测试不依赖 Node；Node/npm 仅用于前端 lint。最终切换证据和空生产数据限制见 [FastAPI 切换记录](fastapi-cutover.md)。

## 最终 Python 验收（2026-10-06，Task 16）

Node 清理后的实际 `scripts/check.ps1` 退出 0：Pyink 35 文件不变、Google
Pylint 无诊断、pytest `226 passed in 19.83s`、三个前端 lint 全通过。
隐藏 Node/npm 后完整 pytest `226 passed in 29.03s`。严格 OpenSpec 校验
有效。全新目录无缓存安装 Python 3.11.16 后，用原端口单 worker 命令
启动，三页与读取 API 返回 200、私有数据路径 404；测试服务器已停止。

8766 隔离浏览器完成 15 项完整流程，原端口的空生产历史与三页检查通过。
生产没有真实旧记录；空目录备份和隔离合成旧数据恢复、成功图字节/ZIP
验证均有记录。契约测试使用从旧 Node 提交实际抓取的 37 条冻结响应，
22 个用例继续检查真实 FastAPI。

## 迁移前 Node 验收历史（2026-10-03）

- `npm test`：15 项 Node 测试通过，覆盖上传内容校验、草稿清理与归档、视觉图片传输、角色与 SK 隔离、任务快照与恢复、图片编辑响应及完整解码、双失败模块的单模块重试、取消、请求超时信号、ZIP 和删除后的草稿清理。
- `node smoke-test.js`：旧文案接口及本机模拟服务商的 2 图 / 3 模块 / 局部失败 / 重试 / ZIP 路径通过。
- `openspec validate generate-product-detail-images --strict`：变更文档通过校验。
- 浏览器：使用本机模拟服务商，验证上传后刷新保留图片、分角色图片测试、AI 帮写并手工修改、生成 3 模块、结果刷新保留、局部失败和单模块重试、ZIP 下载、历史任务列表；模拟服务离线时 AI 帮写保留原文，测试结果未标记成功。

尚需用户用自己的 SK 和账户权限在设置页对视觉模型、生图模型分别运行一次真实图片能力测试，并小规模核对生成外观、响应限制和实际费用。以上模拟结果不代表真实平台已连接成功。

OpenAI Images Edits 的多图 `image[]`、Base64 图片结果、GPT Image 2.5 自定义尺寸与旧 GPT Image 1 固定尺寸分别参考 [官方图像生成指南](https://developers.openai.com/api/docs/guides/image-generation) 和 [图像提示指南](https://developers.openai.com/api/docs/guides/image-prompting)。

## FastAPI 模拟端到端与统一质量门（2026-10-06，Task 15）

复现命令：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`。
该参数只允许本次 PowerShell 子进程执行仓库脚本，不改变用户或机器执行策略。
环境为锁定依赖的 Python 3.11、uv、Node 与 npm 开发依赖；应用使用单个
FastAPI worker。pytest 的发现目录固定为 `tests/`。

`tests/test_e2e.py` 使用临时数据目录和操作系统分配的两个独立
`127.0.0.1` 端口：一端运行真实 Uvicorn/FastAPI，一端运行模拟视觉/生图
HTTP 服务。路由、序列队列、存储、视觉/文本/图片适配器、图片完整解码与
ZIP 导出均为真实实现；未注入适配器替身或访问外部模型服务。

| 验收流程 | 已观察结果 |
| --- | --- |
| 上传两张不同 PNG 原图、读取 WebP 预览、保存分角色配置 | 上传均返回 201；未触发 AI 前服务商请求为零 |
| 三模块：首屏、细节、品牌故事 | 视觉请求只执行一次；JSON 图片块与每次 `image[]` multipart 传递两张原始图片字节和 MIME |
| 第二模块第一次返回 429 | 任务为 `partial`，模块为 `completed / failed / completed`，错误分类为 `rate_limit`，错误中的虚构 SK 被移除 |
| 单模块重试 | 任务为 `completed`；尝试次数 `[1, 2, 1]`；第一、第三模块及视觉分析快照保持一致；生图请求顺序 `1, 2, 3, 2` |
| 图片与 ZIP | 各结果 PNG 可下载并完整解码；部分完成 ZIP 只含第一、第三成品；最终 ZIP 含 manifest、文案清单和三张与下载字节一致的图片 |
| 第二模块生图过程中取消另一个任务 | 返回 `canceled`；尝试次数 `[1, 1, 0]`；第一结果保留；第三模块未调用；取消任务重试返回 400；ZIP 仍含已完成图片 |
| 删除任务与关联草稿 | 任务、成品、原图、ZIP 和草稿读取返回 404；任务目录与关联草稿目录消失；历史列表为空 |
| SK 边界 | 公开角色、任务 JSON、ZIP 各文件及临时目录全部文件均不含虚构 SK |
| 脚本失败注入 | Pyink、Pylint、pytest、npm lint 四步分别失败时退出 17 且后续步骤未运行；全成功时执行四步并退出 0 |

端到端与脚本测试的定向命令 `uv run --locked pytest tests/test_e2e.py -q`
返回 `7 passed`。完整统一质量门的结果在下方记录。

| 统一检查步骤 | 实际输出 / 退出码 |
| --- | --- |
| `uv run --locked pyink --check backend tests` | `35 files would be left unchanged.`；0 |
| `uv run --locked pylint --rcfile=config/google.pylintrc backend` | 无诊断；0 |
| `uv run --locked pytest -q` | `226 passed in 51.66s`；0 |
| `npm run lint` | ESLint、Stylelint、HTML Validate 三项全部通过；0 |
| PowerShell 统一脚本 | 四步依序执行完成；0 |

测试不需要真实 SK，也未验证真实账户权限、模型是否可用、计费、外部限流、
服务商生成外观或真实网络取消行为；取消测试验证本机应用中断当前 HTTP
请求并停止后续模块。真实平台能力仍需在设置页用用户账户分别进行图片测试。
Task 14 的三页浏览器与移动布局证据继续保存在其独立验收目录，Task 15
验证 HTTP 与文件边界，不重复浏览器验收。
