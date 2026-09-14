param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('test', 'full', 'report', 'qa', 'evaluate')]
    [string]$Step,
    [string]$RunVersion = 'v1',
    [string]$Model = 'deepseek-v4-flash',
    [string]$BaseUrl = 'https://api.deepseek.com'
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$recentData = Join-Path $projectDir 'data/eggy_party_appstore_cn_recent_500.json'
$devData = Join-Path $projectDir 'data/day1_split_recent_500/dev_sample.json'
$blindLabels = Join-Path $projectDir 'data/day1_split_recent_500/blind_labels.csv'
$devRun = Join-Path $projectDir "runs/day2_dev_$RunVersion"
$fullRun = Join-Path $projectDir "runs/day2_full_$RunVersion"

function Invoke-PythonStep {
    param([string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Set-ApiEnvironment {
    if (-not $env:LLM_API_KEY) {
        $secret = Read-Host 'Enter DeepSeek API Key (input is hidden)' -AsSecureString
        $env:LLM_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    }
    $env:LLM_BASE_URL = $BaseUrl
    $env:LLM_MODEL = $Model
    $env:LLM_THINKING = 'disabled'
}

function Assert-AnalysisSucceeded {
    param(
        [string]$ResultsPath,
        [int]$RequiredSuccesses
    )
    $rows = Get-Content -LiteralPath $ResultsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $successes = @($rows | Where-Object { $_.status -eq 'ok' }).Count
    $errors = @($rows | Where-Object { $_.status -eq 'error' }).Count
    if ($errors -gt 0 -or $successes -lt $RequiredSuccesses) {
        throw "Analysis incomplete: $successes successful, $errors failed. Check the error above, fix it, then run the same command again."
    }
}

Push-Location $projectDir
try {
    switch ($Step) {
        'test' {
            Set-ApiEnvironment
            Invoke-PythonStep @('main.py', 'analyze', '--input', $devData, '--out', $devRun, '--limit', '10')
            Assert-AnalysisSucceeded -ResultsPath (Join-Path $devRun 'results.json') -RequiredSuccesses 10
            Invoke-PythonStep @('main.py', 'report', '--input', (Join-Path $devRun 'results.json'), '--out', (Join-Path $devRun 'report'))
            Write-Host "Test run completed: $devRun"
        }
        'full' {
            Set-ApiEnvironment
            Invoke-PythonStep @('main.py', 'analyze', '--input', $recentData, '--out', $fullRun, '--limit', '500')
            Assert-AnalysisSucceeded -ResultsPath (Join-Path $fullRun 'results.json') -RequiredSuccesses 500
            Invoke-PythonStep @('main.py', 'report', '--input', (Join-Path $fullRun 'results.json'), '--out', (Join-Path $fullRun 'report'))
            Write-Host "Full analysis completed: $fullRun"
        }
        'report' {
            Invoke-PythonStep @('main.py', 'report', '--input', (Join-Path $fullRun 'results.json'), '--out', (Join-Path $fullRun 'report'))
        }
        'qa' {
            Invoke-PythonStep @('main.py', 'qa-sample', '--input', (Join-Path $fullRun 'results.json'), '--limit', '30', '--out', (Join-Path $fullRun 'qa_sample.csv'))
        }
        'evaluate' {
            Invoke-PythonStep @('main.py', 'evaluate', '--input', (Join-Path $fullRun 'results.json'), '--labels', $blindLabels, '--out', (Join-Path $fullRun 'evaluation.json'))
        }
    }
}
finally {
    Pop-Location
}
