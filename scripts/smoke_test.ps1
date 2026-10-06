# Smoke test against a running server. Needs PowerShell 7+ (pwsh) for -Form.
#   .\scripts\smoke_test.ps1                      # local
#   .\scripts\smoke_test.ps1 -Base https://your-app.onrender.com
param([string]$Base = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"

$health = Invoke-RestMethod "$Base/api/health"
Write-Host "health: ok=$($health.ok) llm=$($health.llm) vision=$($health.vision) ocr=$($health.ocr)"

$cases = @(
  @{ name = "kyc-sms";  expect = "scam"; text = "Your SBI YONO account will be blocked today. Update KYC immediately: http://sbi-kyc-update.xyz/login" },
  @{ name = "upi-pin";  expect = "scam"; text = "You won Rs 5000 cashback! Scan the QR and enter your UPI PIN to receive money." },
  @{ name = "bank-otp"; expect = "low";  text = "483920 is your OTP for Rs 2,499 at AMAZON. Do not share OTP with anyone. -HDFC Bank" },
  @{ name = "friend";   expect = "low";  text = "Are we still on for dinner tomorrow at 8?" }
)

$fail = 0
foreach ($c in $cases) {
  $r = Invoke-RestMethod -Method Post -Uri "$Base/api/check" -Form @{ text = $c.text; lang = "en" }
  $ok = $r.verdict -eq $c.expect
  if (-not $ok) { $fail++ }
  "{0,-10} expected={1,-10} got={2,-10} risk={3,3} ai={4}  {5}" -f $c.name, $c.expect, $r.verdict, $r.risk, $r.ai_used, ($(if ($ok) {"PASS"} else {"FAIL"}))
}
if ($fail) { Write-Host "$fail case(s) failed" -ForegroundColor Red; exit 1 } else { Write-Host "all passed" -ForegroundColor Green }
