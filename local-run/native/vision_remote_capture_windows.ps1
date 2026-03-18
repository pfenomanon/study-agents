param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteImageUrl,
    [string]$ApiToken = "",
    [string]$ProfileId = "",
    [string]$Platform = "",
    [string]$Model = "",
    [string]$OllamaTarget = "",
    [double]$Dpi = 96.0,
    [double]$TopIn = 0.0,
    [double]$BottomIn = 0.0,
    [double]$LeftIn = 0.0,
    [double]$RightIn = 0.0,
    [int]$SessionWebTtlMinutes = 120,
    [switch]$NoSessionWeb,
    [switch]$NoSessionWebOpen,
    [switch]$NoSessionWebQr,
    [switch]$Loop
)

$ErrorActionPreference = "Stop"

if ($Platform -and @("openai", "ollama") -notcontains $Platform) {
    throw "Invalid -Platform '$Platform'. Expected openai or ollama."
}
if ($OllamaTarget -and @("local", "cloud") -notcontains $OllamaTarget) {
    throw "Invalid -OllamaTarget '$OllamaTarget'. Expected local or cloud."
}

function Get-CaptureSessionStartUrl([string]$remoteImageUrl) {
    try {
        $uri = [uri]$remoteImageUrl
    }
    catch {
        throw "Invalid -RemoteImageUrl '$remoteImageUrl'"
    }
    if (-not $uri.Scheme -or -not $uri.Host) {
        throw "Invalid -RemoteImageUrl '$remoteImageUrl'"
    }
    if (@("http", "https") -notcontains $uri.Scheme.ToLowerInvariant()) {
        throw "RemoteImageUrl scheme must be http or https."
    }
    return "{0}://{1}/capture-session/start" -f $uri.Scheme, $uri.Authority
}

function New-RemoteCaptureSession {
    $startUrl = Get-CaptureSessionStartUrl -remoteImageUrl $RemoteImageUrl
    $headers = @{}
    if ($ApiToken) {
        $headers["X-API-Key"] = $ApiToken
    }
    $payload = @{}
    if ($SessionWebTtlMinutes -gt 0) {
        $payload["ttl_minutes"] = $SessionWebTtlMinutes
    }
    $json = ($payload | ConvertTo-Json -Compress)
    $resp = Invoke-RestMethod -Method Post -Uri $startUrl -Headers $headers -ContentType "application/json" -Body $json
    if (-not $resp.ok) {
        $err = if ($resp.error) { [string]$resp.error } else { "Failed to create capture session." }
        throw $err
    }
    if (-not $resp.session_id -or -not $resp.access_code -or -not $resp.access_url) {
        throw "Capture session response missing required fields."
    }
    return $resp
}

function Write-SessionQrPage {
    param(
        [string]$SessionId,
        [string]$AccessCode,
        [string]$AccessUrl,
        [string]$ExpiresAt
    )
    $dir = Join-Path $env:TEMP "study-agents-capture-sessions"
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $htmlPath = Join-Path $dir ("capture_session_{0}_qr.html" -f $SessionId)
    $safeUrl = $AccessUrl.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
    $safeCode = $AccessCode.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
    $safeSession = $SessionId.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
    if ([string]::IsNullOrWhiteSpace($ExpiresAt)) {
        $ExpiresAt = "N/A"
    }
    $safeExpires = $ExpiresAt.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
    $jsUrl = $AccessUrl.Replace("\", "\\").Replace("'", "\'")
    $html = @"
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Capture Session QR</title>
  <style>
    body { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #061229; color: #e6eeff; }
    .wrap { max-width: 680px; margin: 0 auto; padding: 20px; }
    .card { border: 1px solid #2b4778; border-radius: 14px; background: #0d1b33; padding: 16px; }
    .row { margin: 10px 0; }
    .label { font-weight: 700; color: #cfe0ff; margin-bottom: 4px; }
    .value { word-break: break-all; white-space: pre-wrap; }
    #qrcode { margin: 10px auto; width: 300px; min-height: 300px; background: #fff; border-radius: 10px; padding: 12px; display: grid; place-items: center; color: #021126; }
  </style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js" crossorigin="anonymous"></script>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="row"><div class="label">Session ID</div><div class="value">$safeSession</div></div>
      <div class="row"><div class="label">Access Code</div><div class="value">$safeCode</div></div>
      <div class="row"><div class="label">VPS Session URL</div><div class="value">$safeUrl</div></div>
      <div class="row"><div class="label">Expires At (UTC)</div><div class="value">$safeExpires</div></div>
      <div id="qrcode">Loading QR...</div>
    </div>
  </div>
  <script>
    (function () {
      var url = '$jsUrl';
      var target = document.getElementById('qrcode');
      if (window.QRCode) {
        target.innerHTML = '';
        new QRCode(target, { text: url, width: 280, height: 280, correctLevel: QRCode.CorrectLevel.M });
      } else {
        target.textContent = 'QR unavailable. Use URL + access code.';
      }
    })();
  </script>
</body>
</html>
"@
    Set-Content -Path $htmlPath -Value $html -Encoding UTF8
    return $htmlPath
}

function Convert-InchesToPixels([double]$inches, [double]$dpi) {
    if ($inches -le 0) {
        return 0
    }
    return [int][Math]::Round($inches * $dpi)
}

function New-CaptureImage {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds

    $topPx = Convert-InchesToPixels -inches $TopIn -dpi $Dpi
    $bottomPx = Convert-InchesToPixels -inches $BottomIn -dpi $Dpi
    $leftPx = Convert-InchesToPixels -inches $LeftIn -dpi $Dpi
    $rightPx = Convert-InchesToPixels -inches $RightIn -dpi $Dpi

    $x = $screen.X + $leftPx
    $y = $screen.Y + $topPx
    $w = $screen.Width - $leftPx - $rightPx
    $h = $screen.Height - $topPx - $bottomPx

    if ($w -lt 64 -or $h -lt 64) {
        throw "Invalid capture region after margins. Width=$w Height=$h"
    }

    $outFile = Join-Path $env:TEMP ("study-agents-capture-{0}.png" -f ([guid]::NewGuid().ToString("N")))
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $gfx.CopyFromScreen($x, $y, 0, 0, $bmp.Size)
    $bmp.Save($outFile, [System.Drawing.Imaging.ImageFormat]::Png)
    $gfx.Dispose()
    $bmp.Dispose()
    return $outFile
}

function Invoke-RemoteCapture([string]$imagePath) {
    $curlArgs = @(
        "-sS",
        "-X", "POST",
        $RemoteImageUrl,
        "-F", "image=@$imagePath;type=image/png"
    )
    if ($ApiToken) {
        $curlArgs += @("-H", "X-API-Key: $ApiToken")
    }
    if ($ProfileId) {
        $curlArgs += @("-F", "profile_id=$ProfileId")
    }
    if ($Platform) {
        $curlArgs += @("-F", "platform=$Platform")
    }
    if ($Model) {
        $curlArgs += @("-F", "model=$Model")
    }
    if ($OllamaTarget) {
        $curlArgs += @("-F", "ollama_target=$OllamaTarget")
    }
    if ($script:CaptureSessionId) {
        $curlArgs += @("-F", "capture_session_id=$script:CaptureSessionId")
    }

    $raw = & curl.exe @curlArgs
    if ($LASTEXITCODE -ne 0) {
        throw "curl.exe failed with exit code $LASTEXITCODE"
    }
    return $raw
}

function Write-Result([string]$rawJson) {
    try {
        $obj = $rawJson | ConvertFrom-Json
        if ($null -ne $obj.question -and $obj.question -ne "") {
            Write-Host ""
            Write-Host "Question:"
            Write-Host $obj.question
        }
        if ($null -ne $obj.answer -and $obj.answer -ne "") {
            Write-Host ""
            Write-Host "Answer:"
            Write-Host $obj.answer
        }
        if ($null -ne $obj.citations -and $obj.citations.Count -gt 0) {
            Write-Host ""
            Write-Host "Citations:"
            Write-Host ($obj.citations -join ", ")
        }
        return
    }
    catch {
        Write-Host $rawJson
    }
}

if (-not $ApiToken -and $env:REMOTE_API_TOKEN) {
    $ApiToken = $env:REMOTE_API_TOKEN
}
if (-not $ProfileId -and $env:PROFILE_ID) {
    $ProfileId = $env:PROFILE_ID
}

try {
    $remoteUri = [uri]$RemoteImageUrl
    if (
        $remoteUri.Scheme -eq "http" -and
        @("127.0.0.1", "localhost") -notcontains $remoteUri.Host.ToLowerInvariant()
    ) {
        Write-Warning "RemoteImageUrl uses HTTP. Use HTTPS for encrypted transport over untrusted networks."
    }
}
catch {
    throw "Invalid -RemoteImageUrl '$RemoteImageUrl'"
}

$script:CaptureSessionId = ""
if (-not $NoSessionWeb) {
    $session = New-RemoteCaptureSession
    $script:CaptureSessionId = [string]$session.session_id
    $sessionUrl = [string]$session.access_url
    $sessionCode = [string]$session.access_code
    $sessionExpires = [string]$session.expires_at

    Write-Host "Session report URL (VPS): $sessionUrl"
    Write-Host "Session access code: $sessionCode"
    if ($sessionExpires) {
        Write-Host "Session expires (UTC): $sessionExpires"
    }
    if (-not $NoSessionWebQr) {
        $qrHtml = Write-SessionQrPage -SessionId $script:CaptureSessionId -AccessCode $sessionCode -AccessUrl $sessionUrl -ExpiresAt $sessionExpires
        Write-Host "Session QR page: $qrHtml"
        if (-not $NoSessionWebOpen) {
            Start-Process $qrHtml | Out-Null
        }
    }
}

Write-Host "Mode: remote_image (native Windows client)"
Write-Host "Endpoint: $RemoteImageUrl"
if ($ProfileId) {
    Write-Host "Profile: $ProfileId"
}
Write-Host "DPI: $Dpi, Margins(in): top=$TopIn left=$LeftIn right=$RightIn bottom=$BottomIn"

do {
    $img = ""
    try {
        $img = New-CaptureImage
        $raw = Invoke-RemoteCapture -imagePath $img
        Write-Result -rawJson $raw
    }
    finally {
        if ($img -and (Test-Path $img)) {
            Remove-Item -Force $img -ErrorAction SilentlyContinue
        }
    }

    if (-not $Loop) {
        break
    }
    $next = Read-Host "Press Enter to capture again, or type q to quit"
    if ($next -match "^(q|quit|exit)$") {
        break
    }
}
while ($true)
