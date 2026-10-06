# FastAPI 切换与回退记录（2026-10-06）

运行入口已完成 Python 3.11 / FastAPI 切换，使用一个 Uvicorn worker：

```powershell
uv sync --locked --python 3.11
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8765 --workers 1
```

Node 后端、Node 测试与运行依赖已清理。`npm run lint` 保留为前端开发命令。

## 切换前检查与备份

迁移工作树的前置提交为 `9cbfba1`，原生产目录
`F:\蓝星茶叶\电商商品详情页` 的 `master` 为
`3ad8b0ced57a51498a366870a8df3f39ed708064`，检查时工作树干净。
未发现命令行关联项目的 Node 进程，也没有 `127.0.0.1:8765` 监听。
备份前再次检查，状态相同，因此无需停止 Node。未停止其他进程。

实际生产 `data/` 有 **0 个文件、0 个草稿、0 个任务**。没有真实旧草稿、
已完成任务或未完成任务可读取；此次按空数据启动验收。
在启动原端口 FastAPI 前复制整个空目录到：

```text
F:\蓝星茶叶\电商商品详情页\backups\fastapi-cutover\20261006-task16\data\
```

备份根目录中的 `source-sha256.txt` 和 `backup-sha256.txt` 是按相对路径
列出的文件 SHA256 清单，因没有文件，两者均为空。两个清单的 SHA256 均为：

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

源与副本均为 0 文件，空目录确实存在；空清单哈希不代表检查了任何真实图片。
`backups/` 已加入迁移分支 `.gitignore`；生产目录切换前通过本地
`.git/info/exclude` 忽略同一路径，保持其跟踪文件干净。

## 验收顺序与证据

1. 清理前执行实际 `scripts/check.ps1`：Pyink 35 文件、Google Pylint、
   完整 pytest **226 passed in 32.93s**、ESLint/Stylelint/HTML Validate
   全部通过，脚本退出 0。
2. 在独立端口 8766、临时数据目录运行 FastAPI。沿用 Task 14 的实际
   Playwright 浏览器流程，15 项检查通过：六图上传限制、移除/刷新恢复、
   表单校验、模型设置、SK 可见性与保存、能力测试、AI 帮写失败保持原文、
   成功编辑、任务轮询、部分失败、单模块重试、PNG/ZIP 下载、历史/删除、
   取消与零页面异常；390/1280 像素布局无横向溢出。随后停止该测试服务。
3. 完成上方静止数据备份。用迁移分支的 `create_app` 显式指向实际生产
   `data/`，在原端口 8765 启动 Python 3.11 单 worker，所属 PID 为 3796。
   浏览器三页均返回 200，历史为 `{jobs: []}`，390 像素无横向溢出、
   无页面异常。停止该测试服务后，生产目录仍为 0 文件。
4. 在 **另一个隔离目录**复制已有合成 Node 格式样本，增加一个合成完成
   任务并补足展示元数据。草稿样本原创建时间已过期，仅在合成副本中刷新
   时间用于读取；真实数据未改时间。在原端口 8765 启动单 worker，PID
   为 23760，验证下表后停止服务。
5. 上述验证完成后才移除 Node 代码、测试及依赖，运行最终统一质量门、
   严格 OpenSpec 校验和全新 Python 安装/启动验证。

| 合成记录 | 实际结果 |
| --- | --- |
| 草稿 `11111111-1111-4111-8111-111111111111` | 草稿与 WebP 预览读取 200，预览完整解码 |
| 未完成任务 `33333333-3333-4333-8333-333333333333` | `generating` 恢复为 `waiting_credentials`，运行模块恢复为 `pending`，成功模块仍 `completed`，原毫秒创建时间与额外字段保留 |
| 完成任务 `44444444-4444-4444-8444-444444444444` | 保持 `completed`，成功图可完整解码与下载 |
| 两个任务的单图 | 原始字节一致，SHA256 `d98e7fb35c96d67d05d0be4161b617ece5c826eb6d901999994acf50502fa805` |
| 两个 ZIP | 均可下载，ZIP 校验无坏文件，包含 manifest、文案清单和与单图一致的 `images/01-hero.png` |
| 合成结果页浏览器 | 等待凭据/全部完成状态显示正确，两次实际 ZIP 下载成功，无页面异常 |

工作树本地证据目录为 `backups/fastapi-cutover/20261006-task16/`。
其中保存浏览器脚本、截图、`browser-results.json`、`production-browser.json`、
`legacy-validation.json`、`legacy-browser.json`、下载文件及干净 Python 环境。
这些均为忽略文件，实际商品图和实际 SK 未提交。

原端口三页脚本第一次使用不存在的 `#visionProvider`，随后对空白
`#resultMessage` 等待可见导致超时；改为实际 `#providerSelect`、`#jobList`
后完整通过。两次均为验收脚本选择器问题，不是应用失败。

## 冻结 Node 契约

`tests/fixtures/node_contract_responses.json` 在删除 Node 前从提交 `9cbfba1`
的 `server/app.js` 抓取：执行原 `test_contract.py` 全部 **22** 个用例，
保存 **37** 个实际 Node 响应及请求顺序。Node 使用独立临时目录与返回
“连接成功”的合成 `providerFetch`，抓取时 Node/FastAPI 对照全部通过。
快照内的 `secret-test` 仅为虚构测试值。

Python 契约测试直接调用真实 FastAPI，与每个用例的有序快照比较状态码、
JSON 和 MIME，并检查请求及快照完整消费；它不启动 Node，也不以当前
FastAPI 输出计算期待值。沿用原测试对 `/api/roles` 非对象错误文本的例外，
仍检查 400 和页面可读错误形状。实际恢复、队列、图片和 ZIP 的其他测试保留。

为证明快照能捕获行为回归，临时把真实 `/api/test` 响应 `reply` 改为
`legacyReply`：该用例如期失败；快照未完全消费同时触发 teardown 检查。
随后字节级恢复生产文件，22 个契约用例全部通过，最终无后端实现改动。

## 最终检查

- Node 清理后实际统一脚本：Pyink 35 文件不变、Pylint 无诊断、
  **226 passed in 19.83s**、三项前端 lint 全通过，退出 0。
- 隐藏 Node/npm 的 PATH，实际完整 pytest：**226 passed in 29.03s**，
  没有跳过 PowerShell 质量门用例，证明 Python 测试无需 Node。
- `npm ci --ignore-scripts` 按清理后 lockfile 安装 232 个开发包，退出 0。
- `openspec validate migrate-to-fastapi-google-style --strict`：变更有效。
- 在全新 `clean-python/` 目录仅复制 `backend/`、`public/`、
  `pyproject.toml`、`uv.lock`，隐藏 Node/npm 后实际执行
  `uv sync --locked --python 3.11 --no-cache`：安装 40 个 Python 包，
  解释器 **3.11.16**，`shutil.which('node')` 为 `None`。用 README 的
  原端口命令启动单 worker（PID 23368）：三页、目录预设与三个读取 API
  均返回 200，空历史正确，私有 `/data/jobs` 返回 404，随后停止测试服务。
- 重装精简后的 npm 开发依赖后 `npm run lint` 再次通过；lockfile 移除
  73 个旧后端依赖包，保留下来的包版本无变化。
- 本次只验证运行与数据切换；原生产 checkout 的跟踪文件保持在旧提交，
  迁移实现提交在 `codex/fastapi-migration`，分支集成由主任务处理。

真实 SK、真实账户能力、外部模型可用性、计费与生图外观仍未验证。
没有真实旧记录，因此旧数据兼容性证据来自合成样本与自动化测试。

## 可执行回退步骤

此次切换验证通过，未执行回退。需要回退时，先停止唯一写数据的 FastAPI，
保留切换后数据，再恢复已验证的备份，最后运行原 Node 提交。
先确认没有本项目其他进程写入 `data/`。若 8765 有监听，先检查 PID、
命令行和可执行文件位置，再把确认属于本项目的 FastAPI PID 填入下方
`$taskFastApiPid`；默认值不会停止任何监听进程。将整个命令块保存为
`.ps1` 后运行。任一文件操作、清单验证或外部命令失败都会以非零状态停止；
失败后保留现状并检查，不可跳过检查直接启动 Node。

```powershell
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-TaskManifest {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "数据目录不存在：$Root"
    }
    $taskRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
    if ((Get-Item -LiteralPath $taskRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw '数据根目录是链接，停止自动恢复'
    }
    $taskEntries = @(Get-ChildItem -LiteralPath $taskRoot -Recurse -Force)
    if ($taskEntries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
        throw '数据含链接目录或链接文件，停止自动恢复'
    }
    foreach ($taskFile in ($taskEntries | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)) {
        $taskRelative = $taskFile.FullName.Substring($taskRoot.Length + 1).Replace('\', '/')
        $taskHash = (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$taskHash  $taskRelative"
    }
}

function Assert-TaskSnapshot {
    param([string]$Root, [string[]]$Expected)
    $taskActual = @(Get-TaskManifest -Root $Root)
    if (($taskActual -join "`n") -cne ($Expected -join "`n")) {
        throw "数据文件路径或 SHA256 不匹配：$Root"
    }
}

try {
    $taskProduction = 'F:\蓝星茶叶\电商商品详情页'
    $taskProduction = (Resolve-Path -LiteralPath $taskProduction).Path.TrimEnd('\')
    $taskFastApiPid = 0 # 有监听时，改为已核对属于本项目的 FastAPI PID。
    $taskOldCommit = '3ad8b0ced57a51498a366870a8df3f39ed708064'
    $taskBackup = Join-Path $taskProduction 'backups\fastapi-cutover\20261006-task16'
    $taskBackupData = (Resolve-Path -LiteralPath (Join-Path $taskBackup 'data')).Path
    $taskManifestFile = Join-Path $taskBackup 'backup-sha256.txt'
    $taskManifestHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    if ((Get-FileHash -LiteralPath $taskManifestFile -Algorithm SHA256).Hash.ToLowerInvariant() -cne $taskManifestHash) {
        throw '备份清单与切换记录中的哈希不匹配'
    }
    $taskExpected = @(Get-Content -LiteralPath $taskManifestFile)
    Assert-TaskSnapshot -Root $taskBackupData -Expected $taskExpected
    $taskData = Join-Path $taskProduction 'data'
    $taskResolvedData = (Resolve-Path -LiteralPath $taskData).Path
    if ($taskResolvedData -ne $taskData) { throw '生产数据路径不符合预期' }
    $taskPreserved = [IO.Path]::GetFullPath((Join-Path $taskProduction ('backups\fastapi-cutover\post-cutover-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))))
    $taskRollback = Join-Path $taskProduction 'backups\fastapi-cutover\rollback-node'
    foreach ($taskTarget in @($taskBackupData, $taskResolvedData, $taskPreserved, $taskRollback)) {
        if (-not $taskTarget.StartsWith($taskProduction + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "路径超出生产工作区：$taskTarget"
        }
    }
    if ((Test-Path -LiteralPath $taskPreserved) -or (Test-Path -LiteralPath $taskRollback)) {
        throw '保存目录或回退工作树已存在；不能合并或覆盖'
    }
    $taskListeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 8765 })
    if ($taskListeners.Count -gt 0) {
        if ($taskListeners.Count -ne 1 -or $taskFastApiPid -le 0 -or $taskListeners[0].OwningProcess -ne $taskFastApiPid) {
            throw '监听 PID 未核对，停止回退'
        }
        $taskWriter = Get-CimInstance Win32_Process -Filter "ProcessId=$taskFastApiPid"
        if (-not $taskWriter -or $taskWriter.CommandLine -notmatch 'backend\.main:app' -or -not $taskWriter.ExecutablePath.StartsWith($taskProduction + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw '监听进程不是已核对的生产 FastAPI'
        }
        $taskProcess = Get-Process -Id $taskFastApiPid -ErrorAction Stop
        $null = $taskProcess.Handle # 在停止前取得句柄，保留同一个进程对象。
        Stop-Process -InputObject $taskProcess -ErrorAction Stop
        if (-not $taskProcess.WaitForExit(10000)) { throw 'FastAPI 在 10 秒内未退出' }
    }
    if (Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 8765 }) {
        throw '8765 仍有监听，不能恢复或启动'
    }
    Move-Item -LiteralPath $taskResolvedData -Destination $taskPreserved -ErrorAction Stop
    Copy-Item -LiteralPath $taskBackupData -Destination $taskData -Recurse -ErrorAction Stop
    Assert-TaskSnapshot -Root $taskData -Expected $taskExpected
    git -C $taskProduction worktree add --detach $taskRollback $taskOldCommit
    if ($LASTEXITCODE -ne 0) { throw "Git 创建回退工作树失败：$LASTEXITCODE" }
    $taskHead = git -C $taskRollback rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw "Git 读取回退 HEAD 失败：$LASTEXITCODE" }
    if ([string]$taskHead -cne $taskOldCommit) { throw '回退 HEAD 与原 Node 提交不一致' }
    Set-Location -LiteralPath $taskRollback -ErrorAction Stop
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm 安装失败：$LASTEXITCODE" }
    Assert-TaskSnapshot -Root $taskData -Expected $taskExpected
    if (Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 8765 }) {
        throw '8765 被其他进程占用，不能启动'
    }
    node -e "const {createApp}=require('./server/app'); createApp({dataDir:process.argv[1]}).listen(8765,'127.0.0.1');" $taskData
    if ($LASTEXITCODE -ne 0) { throw "Node 回退启动失败：$LASTEXITCODE" }
}
catch {
    Write-Host "回退中止：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
```

本次可信清单为空，因此恢复后必须为 0 文件；清单哈希固定为切换记录中的值。
清单行格式为 `小写 SHA256 + 两个空格 + 相对路径`，路径分隔符为 `/`。
切换后新数据保存在唯一的 `$taskPreserved`；恢复失败时不删除它，也不启动 Node。
测试仅对合成目录执行上述文件操作，真实生产回退没有执行。
停止前取得已核对 PID 的进程对象与句柄，停止后用该对象的
`WaitForExit(10000)` 等待；进程立即退出也无需重新按 PID 查询。
真实停止失败、对象访问错误或等待超时仍会中止回退。

确认三页、历史、图与 ZIP 能读取，再允许写入。回退 Node 也会要求重填内存
SK；回退启动命令显式使用生产 `data/`，不会误读回退工作树内的空目录。
