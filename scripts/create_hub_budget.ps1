[CmdletBinding()]
param(
    [switch]$Apply,
    [Parameter(Mandatory=$true)][string]$AlertEmail,
    [string]$Profile = 'rift-admin',
    [string]$ExpectedAccount = '903859732456',
    [string]$BudgetName = 'rift-hub-monthly-test',
    [decimal]$MonthlyLimitUsd = 30
)

$ErrorActionPreference = 'Stop'
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $aws)) { throw 'AWS CLI was not found.' }
if ($MonthlyLimitUsd -le 0) { throw 'MonthlyLimitUsd must be positive.' }
$identity = & $aws sts get-caller-identity --profile $Profile --region ap-southeast-2 --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'AWS sign-in/profile validation failed.' }
if ($identity.Account -ne $ExpectedAccount) { throw "Refusing wrong AWS project: expected $ExpectedAccount, received $($identity.Account)." }
Write-Host "Would create global monthly cost budget '$BudgetName' at USD $MonthlyLimitUsd with email alerts at 80% actual and 100% forecasted spend."
Write-Host 'AWS Budgets is a billing-level global service; this does not create a regional application resource.'
if (-not $Apply) { return }
$confirm = Read-Host "Type CREATE to create this budget and send notifications to $AlertEmail"
if ($confirm -cne 'CREATE') { Write-Host 'Cancelled; no budget created.'; return }

$tempDir = Join-Path $env:TEMP ('rift-budget-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDir | Out-Null
try {
    $budget = @{
        BudgetName = $BudgetName
        BudgetLimit = @{ Amount = ([string]$MonthlyLimitUsd); Unit = 'USD' }
        TimeUnit = 'MONTHLY'
        BudgetType = 'COST'
        CostFilters = @{}
    } | ConvertTo-Json -Depth 6
    $subscriber = @{ SubscriptionType = 'EMAIL'; Address = $AlertEmail }
    $notifications = @(
        @{ Notification = @{ NotificationType = 'ACTUAL'; ComparisonOperator = 'GREATER_THAN'; Threshold = 80; ThresholdType = 'PERCENTAGE' }; Subscribers = @($subscriber) },
        @{ Notification = @{ NotificationType = 'FORECASTED'; ComparisonOperator = 'GREATER_THAN'; Threshold = 100; ThresholdType = 'PERCENTAGE' }; Subscribers = @($subscriber) }
    ) | ConvertTo-Json -Depth 8
    $budgetFile = Join-Path $tempDir 'budget.json'
    $notificationsFile = Join-Path $tempDir 'notifications.json'
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($budgetFile, $budget, $utf8NoBom)
    [System.IO.File]::WriteAllText($notificationsFile, $notifications, $utf8NoBom)
    & $aws budgets create-budget --account-id $ExpectedAccount --budget "file://$budgetFile" --notifications-with-subscribers "file://$notificationsFile" --profile $Profile --region us-east-1
    if ($LASTEXITCODE -ne 0) { throw 'AWS Budgets creation failed; verify billing access and whether a budget with this name already exists.' }
    Write-Host "Created budget '$BudgetName'. Alerts are email notifications, not a hard spending cap."
} finally {
    Get-ChildItem -LiteralPath $tempDir -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempDir -Force -ErrorAction SilentlyContinue
}
