$base = "http://localhost:8080"
$log = "C:\temp\acceptance.log"
"=== ACCEPTANCE TESTS $(Get-Date) ===" | Out-File $log -Encoding utf8

function Post-Form($s, $url, $body) {
  try { return Invoke-WebRequest -Uri $url -Method POST -Body $body -WebSession $s -MaximumRedirection 0 -ErrorAction Stop } catch { return $_.Exception.Response }
}
function Post-Json($s, $url, $json) {
  try { return Invoke-WebRequest -Uri $url -Method POST -Body $json -ContentType "application/json" -WebSession $s -MaximumRedirection 0 -ErrorAction Stop } catch { return $_.Exception.Response }
}
function Get-API($s, $url) {
  try { return Invoke-WebRequest -Uri $url -WebSession $s -MaximumRedirection 0 -ErrorAction Stop } catch { return $_.Exception.Response }
}
function Log($msg) { $msg | Out-File $log -Append -Encoding utf8; Write-Host $msg }
function Log-Json($label, $obj) { Log "--- $label ---"; Log ($obj | ConvertTo-Json -Depth 5 -Compress) }

# ========================================================
# SETUP: two user sessions
# ========================================================
$s_a = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$s_b = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Log "### Login teacher_a (class_id=1)"
$r = Post-Form $s_a "$base/auth/login" "username=teacher_a&password=teacher123"
Log "Status: $($r.StatusCode)"
$me = Get-API $s_a "$base/auth/me"
Log "teacher_a /me -> $($me.Content)"

Log "### Login student_b1 (class_id=2)"
$r2 = Post-Form $s_b "$base/auth/login" "username=student_b1&password=student123"
Log "Status: $($r2.StatusCode)"
$me2 = Get-API $s_b "$base/auth/me"
Log "student_b1 /me -> $($me2.Content)"

# ========================================================
# ACCEPTANCE 1: keyword / vector / hybrid 三种检索
# ========================================================
Log ""
Log "========== ACCEPTANCE 1: 三种检索 =========="

Log "--- [1a] keyword 原词 '同分母分数相加减' ---"
$r = Get-API $s_a "$base/api/search?mode=keyword&q=$([System.Uri]::EscapeDataString('同分母分数相加减'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) total=$($b.total) mode=$($b.mode)"
$b.hits | Select-Object -First 3 | ForEach-Object { Log ("  [kw hit] mid={0} title={1} score={2:N4} excerpt={3}" -f $_.material_id, $_.material_title, $_.score, $_.excerpt.Substring(0,[Math]::Min(50,$_.excerpt.Length))) }

Log "--- [1b] keyword 同义改写 '分母相同的分数加减法' ---"
$r = Get-API $s_a "$base/api/search?mode=keyword&q=$([System.Uri]::EscapeDataString('分母相同的分数加减法'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) (keyword 用同义改写应落空或减少)"

Log "--- [1c] vector 同义改写 '分母相同的分数加减法' ---"
$r = Get-API $s_a "$base/api/search?mode=vector&q=$([System.Uri]::EscapeDataString('分母相同的分数加减法'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) mode=$($b.mode) degraded=$($b.degraded) reason=$($b.reason)"
$b.hits | Select-Object -First 3 | ForEach-Object { Log ("  [vec hit] mid={0} title={1} score={2:N4}" -f $_.material_id, $_.material_title, $_.score) }

Log "--- [1d] hybrid 同义改写 '分母相同的分数加减法' ---"
$r = Get-API $s_a "$base/api/search?mode=hybrid&q=$([System.Uri]::EscapeDataString('分母相同的分数加减法'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) mode=$($b.mode)"
$b.hits | Select-Object -First 5 | ForEach-Object { Log ("  [hyb hit] mid={0} title={1} score={2:N4} src_mode={3}" -f $_.material_id, $_.material_title, $_.score, $_.mode) }

Log "--- [1e] hybrid 空 query ---"
try { $r = Get-API $s_a "$base/api/search?mode=hybrid&q="; Log "Status: $($r.StatusCode) BODY: $($r.Content)" } catch { Log "Status: $($_.Exception.Response.StatusCode.value__) (400 expected)" }

Log "--- [1f] 默认 mode (不指定) ---"
$r = Get-API $s_a "$base/api/search?q=$([System.Uri]::EscapeDataString('分子'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "mode=$($b.mode) hits=$($b.hits.Count)"

Log "--- [1g] 非法 mode=xxx ---"
try { $r = Get-API $s_a "$base/api/search?mode=fake&q=x"; Log "Status: $($r.StatusCode) BODY: $($r.Content)" } catch { Log "Status: $($_.Exception.Response.StatusCode.value__)" }

# ========================================================
# ACCEPTANCE 2: 停用 Qdrant → keyword 正常 / vector+hybrid 503
# ========================================================
Log ""
Log "========== ACCEPTANCE 2: 停用 Qdrant =========="
# Stop Qdrant container
docker compose stop qdrant 2>&1 | Out-Null
Start-Sleep 3
Log "Qdrant container stopped."

Log "--- [2a] keyword 原词 (Qdrant 已停) ---"
$r = Get-API $s_a "$base/api/search?mode=keyword&q=$([System.Uri]::EscapeDataString('同分母分数相加减'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) (keyword 应不受 Qdrant 影响)"

Log "--- [2b] vector (Qdrant 已停) ---"
try { $r = Get-API $s_a "$base/api/search?mode=vector&q=$([System.Uri]::EscapeDataString('分数'))"; Log "Status: $($r.StatusCode) BODY: $($r.Content)" } catch { Log "Status: $($_.Exception.Response.StatusCode.value__) (应 503)" }

Log "--- [2c] hybrid (Qdrant 已停) ---"
try { $r = Get-API $s_a "$base/api/search?mode=hybrid&q=$([System.Uri]::EscapeDataString('分数'))"; Log "Status: $($r.StatusCode) BODY: $($r.Content)" } catch { Log "Status: $($_.Exception.Response.StatusCode.value__) (应 503)" }

# Restart Qdrant
docker compose start qdrant 2>&1 | Out-Null
Start-Sleep 5
Log "Qdrant restarted."

Write-Host "=== ACCEPTANCE 1+2 DONE. Log: $log ==="
