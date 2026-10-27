$base = "http://localhost:8080"
$log = "C:\temp\acceptance.log"
"=== ACCEPTANCE TESTS $(Get-Date) ===" | Out-File $log -Encoding utf8

# --- JWT helpers: every request carries Authorization: Bearer <token> ---
function New-Headers($tok) {
  if ($tok) { return @{ Authorization = "Bearer $tok" } }
  return @{}
}

# One HTTP round-trip. On non-2xx we read the body out of the error response so
# 401 / 503 bodies stay visible in the log (Invoke-WebRequest alone hides them).
function Send($method, $tok, $url, $body, $contentType) {
  $h = New-Headers $tok
  try {
    if ($body -ne $null) {
      return Invoke-WebRequest -Uri $url -Method $method -Headers $h -Body $body -ContentType $contentType -MaximumRedirection 0 -ErrorAction Stop
    }
    return Invoke-WebRequest -Uri $url -Method $method -Headers $h -MaximumRedirection 0 -ErrorAction Stop
  } catch {
    $resp = $_.Exception.Response
    if ($resp -eq $null) { throw }
    $content = ""
    # PS 5.1 surfaces the response body of a failed Invoke-WebRequest here.
    if ($_.ErrorDetails -ne $null -and $_.ErrorDetails.Message) {
      $content = $_.ErrorDetails.Message
    } else {
      try { $sr = New-Object System.IO.StreamReader($resp.GetResponseStream()); $content = $sr.ReadToEnd() } catch {}
    }
    return [PSCustomObject]@{ StatusCode = [int]$resp.StatusCode; Content = $content; Headers = $resp.Headers }
  }
}

function QS($tok, $url) { return Send "GET" $tok $url $null $null }
function PJ($tok, $url, $json) { return Send "POST" $tok $url $json "application/json; charset=utf-8" }
function PF($tok, $url, $body) { return Send "POST" $tok $url $body "application/x-www-form-urlencoded" }

function Login($user, $pass) {
  $json = "{""username"":""$user"",""password"":""$pass""}"
  $r = PJ $null "$base/api/login" $json
  if ($r.StatusCode -ne 200) { throw "login failed for $user : $($r.StatusCode) $($r.Content)" }
  return ($r.Content | ConvertFrom-Json).token
}

function Log($msg) { $msg | Out-File $log -Append -Encoding utf8; Write-Host $msg }
function Log-Json($label, $obj) { Log "--- $label ---"; Log ($obj | ConvertTo-Json -Depth 5 -Compress) }

# ========================================================
# SETUP: two identities (JWT, no cookie session)
# ========================================================
Log "### Login teacher_a (class_id=1) -> Bearer token"
$tok_a = Login "teacher_a" "teacher123"
Log "token len: $($tok_a.Length)"
$me = QS $tok_a "$base/api/me"
Log "teacher_a /api/me -> $($me.Content)"

Log "### Login student_b1 (class_id=2) -> Bearer token"
$tok_b = Login "student_b1" "student123"
Log "token len: $($tok_b.Length)"
$me2 = QS $tok_b "$base/api/me"
Log "student_b1 /api/me -> $($me2.Content)"

# ========================================================
# ACCEPTANCE 0: no token on a key endpoint
# ========================================================
Log ""
Log "========== ACCEPTANCE 0: 无 token =========="
Log "--- [0a] GET /api/search 无 Authorization ---"
$r = QS "" "$base/api/search?mode=keyword&q=x"
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (expect 401 token_expired)"

Log "--- [0b] GET /materials/api 无 Authorization ---"
$r = QS "" "$base/materials/api"
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (expect 401 token_expired)"

Log "--- [0c] 篡改 token 末 4 位 ---"
$r = QS ($tok_a.Substring(0, $tok_a.Length - 4) + "aaaa") "$base/api/me"
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (expect 401 token_expired)"

# ========================================================
# ACCEPTANCE 1: keyword / vector / hybrid 三种检索
# ========================================================
Log ""
Log "========== ACCEPTANCE 1: 三种检索 =========="

Log "--- [1a] keyword 原词 '同分母分数相加减' ---"
$r = QS $tok_a "$base/api/search?mode=keyword&q=$([System.Uri]::EscapeDataString('同分母分数相加减'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) total=$($b.total) mode=$($b.mode)"
$b.hits | Select-Object -First 3 | ForEach-Object { Log ("  [kw hit] mid={0} title={1} score={2:N4} excerpt={3}" -f $_.material_id, $_.material_title, $_.score, $_.excerpt.Substring(0,[Math]::Min(50,$_.excerpt.Length))) }

Log "--- [1b] keyword 同义改写 '分母相同的分数加减法' ---"
$r = QS $tok_a "$base/api/search?mode=keyword&q=$([System.Uri]::EscapeDataString('分母相同的分数加减法'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) (keyword 用同义改写应落空或减少)"

Log "--- [1c] vector 同义改写 '分母相同的分数加减法' ---"
$r = QS $tok_a "$base/api/search?mode=vector&q=$([System.Uri]::EscapeDataString('分母相同的分数加减法'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) mode=$($b.mode) degraded=$($b.degraded) reason=$($b.reason)"
$b.hits | Select-Object -First 3 | ForEach-Object { Log ("  [vec hit] mid={0} title={1} score={2:N4}" -f $_.material_id, $_.material_title, $_.score) }

Log "--- [1d] hybrid 同义改写 '分母相同的分数加减法' ---"
$r = QS $tok_a "$base/api/search?mode=hybrid&q=$([System.Uri]::EscapeDataString('分母相同的分数加减法'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) mode=$($b.mode)"
$b.hits | Select-Object -First 5 | ForEach-Object { Log ("  [hyb hit] mid={0} title={1} score={2:N4} src_mode={3}" -f $_.material_id, $_.material_title, $_.score, $_.mode) }

Log "--- [1e] hybrid 空 query ---"
$r = QS $tok_a "$base/api/search?mode=hybrid&q="
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (expect 400)"

Log "--- [1f] 默认 mode (不指定) ---"
$r = QS $tok_a "$base/api/search?q=$([System.Uri]::EscapeDataString('分子'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "mode=$($b.mode) hits=$($b.hits.Count)"

Log "--- [1g] 非法 mode=xxx ---"
$r = QS $tok_a "$base/api/search?mode=fake&q=x"
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (expect 400)"

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
$r = QS $tok_a "$base/api/search?mode=keyword&q=$([System.Uri]::EscapeDataString('同分母分数相加减'))"
Log "Status: $($r.StatusCode)"
$b = $r.Content | ConvertFrom-Json
Log "hits=$($b.hits.Count) (keyword 应不受 Qdrant 影响)"

Log "--- [2b] vector (Qdrant 已停) ---"
$r = QS $tok_a "$base/api/search?mode=vector&q=$([System.Uri]::EscapeDataString('分数'))"
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (应 503)"

Log "--- [2c] hybrid (Qdrant 已停) ---"
$r = QS $tok_a "$base/api/search?mode=hybrid&q=$([System.Uri]::EscapeDataString('分数'))"
Log "Status: $($r.StatusCode) BODY: $($r.Content)  (应 503)"

# Restart Qdrant
docker compose start qdrant 2>&1 | Out-Null
Start-Sleep 5
Log "Qdrant restarted."

Write-Host "=== ACCEPTANCE 1+2 DONE. Log: $log ==="