"""Execute rollback documentation against isolated files and command doubles."""

import hashlib
import os
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_OLD_COMMIT = "3ad8b0ced57a51498a366870a8df3f39ed708064"
_EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _run_rollback(tmp_path, scenario):
    """Use the actual documented blocks with only sandbox path/hash inputs."""
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if os.name != "nt" or not shell:
        pytest.skip("Rollback runbook requires Windows PowerShell")
    production = tmp_path / "production"
    data = production / "data"
    data.mkdir(parents=True)
    (data / "new.txt").write_bytes(b"post-cutover data")
    backup = production / "backups/fastapi-cutover/20261006-task16"
    (backup / "data").mkdir(parents=True)
    manifest = b""
    if scenario == "success_nonempty":
        image = b"synthetic legacy image"
        (backup / "data/legacy.png").write_bytes(image)
        manifest = (
            hashlib.sha256(image).hexdigest() + "  legacy.png\n"
        ).encode()
    (backup / "backup-sha256.txt").write_bytes(manifest)
    if scenario == "backup_corrupt":
        (backup / "data/unexpected.txt").write_bytes(b"unexpected")
    if scenario == "manifest_corrupt":
        (backup / "backup-sha256.txt").write_bytes(b"invalid manifest")
    preserved = (
        production / "backups/fastapi-cutover/post-cutover-20000101-000000"
    )
    if scenario == "preserve_collision":
        preserved.mkdir()
        (preserved / "existing.txt").write_bytes(b"must retain")
    document = (_ROOT / "docs/fastapi-cutover.md").read_text("utf-8")
    rollback = document.split("## 可执行回退步骤", 1)[1]
    blocks = re.findall(r"```powershell\n(.*?)\n```", rollback, re.S)
    assert blocks
    script = "\n".join(blocks)
    production_literal = "'F:\\蓝星茶叶\\电商商品详情页'"
    assert script.count(production_literal) == 1, "Unsafe sandbox path routing"
    script = script.replace(production_literal, _quote(production)).replace(
        _EMPTY_HASH, hashlib.sha256(manifest).hexdigest()
    )
    assert "F:\\蓝星茶叶\\电商商品详情页" not in script
    if scenario == "authorized_fast_exit":
        assert script.count("$taskFastApiPid = 0") == 1
        script = script.replace(
            "$taskFastApiPid = 0", "$taskFastApiPid = 424242"
        )
    marker = tmp_path / "node-started.txt"
    prelude = f"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$taskScenario = {_quote(scenario)}
$taskMarker = {_quote(marker)}
$global:taskStopped = $false
$taskProcessDouble = [pscustomobject]@{{ Id = 424242; Handle = 1; HasExited = $false }}
$taskProcessDouble | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {{
    param([int]$Milliseconds)
    if ($Milliseconds -le 0) {{ throw 'Wait requires a positive timeout' }}
    return $this.HasExited
}}
function Get-Date {{ return '20000101-000000' }}
function Get-NetTCPConnection {{
    if ($taskScenario -eq 'authorized_fast_exit' -and -not $global:taskStopped) {{
        [pscustomobject]@{{ LocalPort = 8765; OwningProcess = 424242 }}
    }}
}}
function Get-CimInstance {{
    if ($taskScenario -ne 'authorized_fast_exit') {{ throw 'Process control is forbidden in sandbox' }}
    [pscustomobject]@{{
        ProcessId = 424242
        CommandLine = 'python -m uvicorn backend.main:app'
        ExecutablePath = Join-Path $taskProduction '.venv\\Scripts\\python.exe'
    }}
}}
function Get-Process {{
    [CmdletBinding()]
    param([int]$Id)
    if ($taskScenario -ne 'authorized_fast_exit' -or $Id -ne 424242 -or $global:taskStopped) {{
        throw 'NoProcessFoundForGivenId'
    }}
    $taskProcessDouble
}}
function Stop-Process {{
    [CmdletBinding()]
    param([int]$Id, [object]$InputObject)
    if ($taskScenario -ne 'authorized_fast_exit' -or ($Id -ne 424242 -and $InputObject -ne $taskProcessDouble)) {{
        throw 'Stopping a real process is forbidden in sandbox'
    }}
    $global:taskStopped = $true
    $taskProcessDouble.HasExited = $true
}}
function Wait-Process {{
    [CmdletBinding()]
    param([int]$Id, [object]$InputObject, [int]$Timeout)
    if ($taskScenario -ne 'authorized_fast_exit') {{ throw 'Waiting for a real process is forbidden in sandbox' }}
    if ($PSBoundParameters.ContainsKey('Id') -and $global:taskStopped) {{
        throw 'NoProcessFoundForGivenId'
    }}
    if ($InputObject -ne $taskProcessDouble -or -not $InputObject.HasExited) {{
        throw 'Wait requires the captured authorized process object'
    }}
}}
function Move-Item {{
    [CmdletBinding()]
    param($LiteralPath, $Destination)
    if ($taskScenario -eq 'move_failure') {{ Write-Error 'Injected move failure'; return }}
    Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
}}
function Copy-Item {{
    [CmdletBinding()]
    param($LiteralPath, $Destination, [switch]$Recurse)
    if ($taskScenario -eq 'copy_failure') {{ Write-Error 'Injected copy failure'; return }}
    Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Recurse:$Recurse
    if ($taskScenario -eq 'copy_corrupt') {{
        [System.IO.File]::WriteAllText((Join-Path $Destination 'unexpected.txt'), 'corrupt restored data')
    }}
}}
function git {{
    $global:LASTEXITCODE = 0
    if ($args -contains 'add') {{
        if ($taskScenario -eq 'worktree_failure') {{ $global:LASTEXITCODE = 17; return }}
        if ($taskScenario -ne 'location_failure') {{
            New-Item -ItemType Directory -Path $taskRollback | Out-Null
        }}
    }} elseif ($args -contains 'rev-parse') {{
        if ($taskScenario -eq 'head_mismatch') {{ return 'incorrect-head' }}
        return '{_OLD_COMMIT}'
    }}
}}
function npm {{
    $global:LASTEXITCODE = 0
    if ($taskScenario -eq 'npm_failure') {{ $global:LASTEXITCODE = 17 }}
}}
function node {{
    [System.IO.File]::WriteAllText($taskMarker, 'started')
    $global:LASTEXITCODE = 0
}}
"""
    path = tmp_path / "rollback-sandbox.ps1"
    path.write_text(prelude + "\n" + script, encoding="utf-8-sig")
    environment = dict(os.environ)
    # A pwsh host can omit Windows PowerShell's built-in module directory from
    # its inherited PSModulePath. The documented script uses standard cmdlets.
    modules = pathlib.Path(environment["SYSTEMROOT"]) / (
        "System32/WindowsPowerShell/v1.0/Modules"
    )
    environment["PSModulePath"] = (
        str(modules) + os.pathsep + environment.get("PSModulePath", "")
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return result, marker, data, preserved, manifest


@pytest.mark.parametrize(
    "scenario",
    [
        "backup_corrupt",
        "manifest_corrupt",
        "preserve_collision",
        "move_failure",
        "copy_failure",
        "copy_corrupt",
        "worktree_failure",
        "head_mismatch",
        "location_failure",
        "npm_failure",
    ],
)
def test_rollback_stops_before_node_on_any_failed_gate(tmp_path, scenario):
    """Bad data or a failed command must never start the rollback writer."""
    result, marker, data, preserved, _ = _run_rollback(tmp_path, scenario)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not marker.exists(), result.stdout + result.stderr
    if scenario in {"backup_corrupt", "manifest_corrupt", "move_failure"}:
        assert (data / "new.txt").read_bytes() == b"post-cutover data"
    elif scenario == "preserve_collision":
        assert (data / "new.txt").read_bytes() == b"post-cutover data"
        assert (preserved / "existing.txt").read_bytes() == b"must retain"
    else:
        assert (preserved / "new.txt").read_bytes() == b"post-cutover data"


@pytest.mark.parametrize(
    "scenario", ["success_empty", "success_nonempty", "authorized_fast_exit"]
)
def test_rollback_starts_only_after_verified_restore(tmp_path, scenario):
    """Verified data is restored and later data preserved before startup."""
    result, marker, data, preserved, manifest = _run_rollback(
        tmp_path, scenario
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
    assert (preserved / "new.txt").read_bytes() == b"post-cutover data"
    assert not (data / "new.txt").exists()
    if manifest:
        assert (data / "legacy.png").read_bytes() == b"synthetic legacy image"
    else:
        assert not list(data.rglob("*"))
