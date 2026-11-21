# Operator-only installed entry diagnostic controller; execute only with specific authorization.
# Invoke through the authorized PowerShell tool. Never change ExecutionPolicy or eval a blocked file.
param([Parameter(Mandatory)][string]$Kit, [Parameter(Mandatory)][string]$Install, [Parameter(Mandatory)][string]$Workspace, [Parameter(Mandatory)][string]$ControlName, [Parameter(Mandatory)][string]$RunName)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$phaseKeys = @('status','failure_code','stdout_bytes','stderr_bytes','natural_exit','natural_exit_zero','host_exited','owned_job_empty','handles_closed','io_threads_stopped','cleanup_complete')
$codes = @('PATH_REJECTED','INSTALL_REJECTED','ROOT_REJECTED','PROTOCOL_REJECTED','IO_FAILED','DEADLINE','NONZERO_EXIT','EMPTY_HELP','HELP_NOT_OBSERVED','CLEANUP_UNCERTAIN','INTERNAL_FAILURE','UNSUPPORTED')
function Assert-Keys($value, $allowed, $required) {
    if ($value.ValueKind -ne [Text.Json.JsonValueKind]::Object) { throw 'INVALID' }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($entry in $value.EnumerateObject()) {
        if (-not $seen.Add($entry.Name) -or -not ($allowed -ccontains $entry.Name)) { throw 'INVALID' }
    }
    foreach ($name in $required) { if (-not $seen.Contains($name)) { throw 'INVALID' } }
}
function Read-Phase($value) {
    Assert-Keys $value $phaseKeys $phaseKeys
    $out = [ordered]@{}
    $status = $value.GetProperty('status')
    if ($status.ValueKind -ne [Text.Json.JsonValueKind]::String -or -not (@('FAILED','OBSERVED') -ccontains $status.GetString())) { throw 'INVALID' }
    $out.status = $status.GetString()
    $failure = $value.GetProperty('failure_code')
    if ($failure.ValueKind -eq [Text.Json.JsonValueKind]::Null) { $out.failure_code = $null }
    elseif ($failure.ValueKind -eq [Text.Json.JsonValueKind]::String -and $codes -ccontains $failure.GetString()) { $out.failure_code = $failure.GetString() }
    else { throw 'INVALID' }
    foreach ($name in @('stdout_bytes','stderr_bytes')) {
        $field = $value.GetProperty($name); [long]$number = 0
        if ($field.ValueKind -ne [Text.Json.JsonValueKind]::Number -or -not $field.TryGetInt64([ref]$number) -or $number -lt 0 -or $number -gt 1052672) { throw 'INVALID' }
        $out[$name] = $number
    }
    foreach ($name in @('natural_exit','natural_exit_zero','host_exited','owned_job_empty','handles_closed','io_threads_stopped','cleanup_complete')) {
        $field = $value.GetProperty($name)
        if ($field.ValueKind -eq [Text.Json.JsonValueKind]::Null -and @('host_exited','owned_job_empty') -ccontains $name) { $out[$name] = $null }
        elseif (@([Text.Json.JsonValueKind]::True,[Text.Json.JsonValueKind]::False) -contains $field.ValueKind) { $out[$name] = $field.GetBoolean() }
        else { throw 'INVALID' }
    }
    if ($out.natural_exit_zero -and -not $out.natural_exit) { throw 'INVALID' }
    if ($out.cleanup_complete) {
        foreach ($name in @('host_exited','owned_job_empty','handles_closed','io_threads_stopped')) { if ($out[$name] -ne $true) { throw 'INVALID' } }
    }
    if ($out.status -ceq 'OBSERVED') {
        if ($out.natural_exit -and -not $out.natural_exit_zero) { throw 'INVALID' }
        if ($null -ne $out.failure_code) { throw 'INVALID' }
        foreach ($name in @('host_exited','owned_job_empty','handles_closed','io_threads_stopped','cleanup_complete')) { if ($out[$name] -ne $true) { throw 'INVALID' } }
    } elseif ($null -eq $out.failure_code) { throw 'INVALID' }
    return $out
}
function Read-Report([string]$text, [int]$exitCode) {
    $doc = [Text.Json.JsonDocument]::Parse($text)
    try {
        $root = $doc.RootElement
        Assert-Keys $root @('status','failure_code','help','initialize') @('status')
        $statusValue = $root.GetProperty('status')
        if ($statusValue.ValueKind -ne [Text.Json.JsonValueKind]::String) { throw 'INVALID' }
        $status = $statusValue.GetString()
        if (@('PREFLIGHT_REJECTED','UNSUPPORTED') -ccontains $status) {
            Assert-Keys $root @('status','failure_code') @('status','failure_code')
            $failure = $root.GetProperty('failure_code')
            if ($exitCode -ne 1 -or $failure.ValueKind -ne [Text.Json.JsonValueKind]::String -or -not ($codes -ccontains $failure.GetString())) { throw 'INVALID' }
            return [ordered]@{ status=$status; failure_code=$failure.GetString() }
        }
        if (-not (@('FAILED','OBSERVED') -ccontains $status)) { throw 'INVALID' }
        Assert-Keys $root @('status','help','initialize') @('status','help')
        $out = [ordered]@{ status=$status; help=(Read-Phase ($root.GetProperty('help'))) }
        $initialize = [Text.Json.JsonElement]::new()
        if ($root.TryGetProperty('initialize', [ref]$initialize)) { $out.initialize = Read-Phase $initialize }
        if ($out.Contains('initialize') -and ($out.help.status -cne 'OBSERVED' -or -not $out.help.natural_exit_zero)) { throw 'INVALID' }
        if ($status -ceq 'OBSERVED') {
            if ($exitCode -ne 0 -or -not $out.Contains('initialize') -or $out.initialize.status -cne 'OBSERVED') { throw 'INVALID' }
        } elseif ($exitCode -ne 1 -or ($out.help.status -ceq 'OBSERVED' -and (-not $out.Contains('initialize') -or $out.initialize.status -cne 'FAILED'))) { throw 'INVALID' }
        return $out
    } finally { $doc.Dispose() }
}
function Check-Ancestors([string]$path) {
    $item = [IO.DirectoryInfo]::new($path)
    while ($null -ne $item) {
        if (-not $item.Exists -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'INVALID' }
        $item = $item.Parent
    }
}
$process = [Diagnostics.Process]::new()
$retained = $null
$attempted = $false
$controller = 'UNVERIFIED'
$signalled = $false
$report = $null
$summaryPath = $null
$outBytes = $null
try {
    if ($ControlName -cnotmatch '^NativeHarnessControl-[a-zA-Z0-9-]+$' -or $RunName -cnotmatch '^NativeHarnessRun-[a-zA-Z0-9-]+$') { throw 'INVALID' }
    Check-Ancestors $Workspace
    Check-Ancestors $Kit
    $control = Join-Path $Workspace $ControlName
    $run = Join-Path $Workspace $RunName
    if ([IO.Directory]::Exists($control) -or [IO.File]::Exists($control) -or [IO.Directory]::Exists($run) -or [IO.File]::Exists($run)) { throw 'INVALID' }
    # Exact kit hashes and install signature/inventory are checked separately before this command.
    $null = New-Item -ItemType Directory -Path $control -ErrorAction Stop
    $summaryPath = Join-Path $control 'summary.json'
    $intent = [IO.File]::Open((Join-Path $control 'launch.intent'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes("single-authorized-controller; no retry`n")
        $intent.Write($bytes,0,$bytes.Length); $intent.Flush($true)
    } finally { $intent.Dispose() }
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = Join-Path $Kit 'runtime/python.exe'
    $psi.WorkingDirectory = $Kit
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    foreach ($value in @('-I','-B',(Join-Path $Kit 'launcher.py'),'--install-root',$Install,'--work-root',$Workspace,'--run-root',$run)) { $psi.ArgumentList.Add($value) }
    $process.StartInfo = $psi
    $attempted = $true
    if (-not $process.Start()) { throw 'INVALID' }
    $retained = $process.SafeHandle
    $process.StandardInput.Close()
    # Reject at the byte cap, without another read to probe past the limit.
    $outBuf = [byte[]]::new(4096); $errBuf = [byte[]]::new(4096)
    [int]$outCount=0; [int]$errCount=0; $outEnded=$false; $errEnded=$false
    $outTask = $process.StandardOutput.BaseStream.ReadAsync($outBuf,0,[Math]::Min($outBuf.Length,16384-$outCount))
    $errTask = $process.StandardError.BaseStream.ReadAsync($errBuf,0,[Math]::Min($errBuf.Length,16384-$errCount))
    $outBytes = [IO.MemoryStream]::new()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        if ($timer.Elapsed.TotalSeconds -ge 120) { $controller='TIMEOUT'; throw 'INVALID' }
        if (-not $outEnded -and $outTask.IsCompleted) {
            $count = $outTask.GetAwaiter().GetResult()
            if ($count -eq 0) { $outEnded=$true }
            else {
                $outCount += $count
                if ($outCount -ge 16384) { $controller='OUTPUT_LIMIT'; throw 'INVALID' }
                $outBytes.Write($outBuf,0,$count)
                $outTask = $process.StandardOutput.BaseStream.ReadAsync($outBuf,0,[Math]::Min($outBuf.Length,16384-$outCount))
            }
        }
        if (-not $errEnded -and $errTask.IsCompleted) {
            $count = $errTask.GetAwaiter().GetResult()
            if ($count -eq 0) { $errEnded=$true }
            else {
                $errCount += $count
                if ($errCount -ge 16384) { $controller='OUTPUT_LIMIT'; throw 'INVALID' }
                $errTask = $process.StandardError.BaseStream.ReadAsync($errBuf,0,[Math]::Min($errBuf.Length,16384-$errCount))
            }
        }
        if ($outEnded -and $errEnded -and $process.WaitForExit(0)) { break }
        Start-Sleep -Milliseconds 20
    }
    $text = [Text.UTF8Encoding]::new($false,$true).GetString($outBytes.ToArray())
    $report = Read-Report $text $process.ExitCode
    $controller = 'COMPLETED'
} catch {
    # No exception/raw stream text is returned. Keep the preselected run tag consumed.
} finally {
    # A Start interruption is not proof no child exists. Recover only this object's handle.
    if ($attempted -and $null -eq $retained) { try { $retained=$process.SafeHandle } catch {} }
    if ($null -ne $retained -and -not $retained.IsInvalid -and -not $retained.IsClosed) {
        try {
            if (-not $process.WaitForExit(0)) { $process.Kill() }
            $signalled = $process.WaitForExit(5000)
        } catch { $signalled=$false }
    }
    try { $process.Dispose() } catch { $signalled=$false }
    if ($null -ne $outBytes) { $outBytes.Dispose() }
}
if ($controller -ceq 'COMPLETED' -and $signalled -and $null -ne $report) {
    $safe = ConvertTo-Json -InputObject $report -Depth 5 -Compress
} else {
    $safe = ConvertTo-Json -InputObject ([ordered]@{controller=$controller;controller_exited=$signalled;native_cleanup='UNKNOWN'}) -Compress
}
if ($null -ne $summaryPath) {
    try {
        $file = [IO.File]::Open($summaryPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $bytes=[Text.Encoding]::UTF8.GetBytes($safe+"`n"); $file.Write($bytes,0,$bytes.Length); $file.Flush($true) } finally { $file.Dispose() }
    } catch {} # Never print an arbitrary I/O exception.
}
[Console]::WriteLine($safe)
# No directory removal, PID lookup, tree kill, fallback, repeated Start or native RPC here.

if ($controller -cne 'COMPLETED' -or -not $signalled -or $null -eq $report -or $report.status -cne 'OBSERVED') { exit 1 }
exit 0
