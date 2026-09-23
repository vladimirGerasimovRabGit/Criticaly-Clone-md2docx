# Вставьте весь текст в PowerShell целиком (не запускайте как файл).
# Перед вставкой заполните три строки ниже.

$ApiKey = 'ВСТАВЬТЕ-КЛЮЧ'
$BaseUrl = 'https://svoivantiprod1.svo.air.loc/HEAT/api/rest/'
$SkipCertificateCheck = $true
$TestWrite = $false

$OutDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ivanti-probe-out'
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ([string]::IsNullOrWhiteSpace($ApiKey) -or $ApiKey -eq 'ВСТАВЬТЕ-КЛЮЧ') {
    Write-Host 'Заполните $ApiKey в первых строках и вставьте скрипт снова.' -ForegroundColor Yellow
    return
}

if ($SkipCertificateCheck) {
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $script:IvantiSkipCert = $true
    }
    else {
        if (-not ('TrustAllCertsPolicy' -as [type])) {
            Add-Type @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAllCertsPolicy : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint sp, X509Certificate cert, WebRequest req, int problem) { return true; }
}
"@
        }
        [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
        $script:IvantiSkipCert = $false
    }
}

function New-IvantiDir($Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

function Write-IvantiStep($Message) {
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Invoke-Ivanti {
    param(
        [string]$Method = 'GET',
        [string]$Url,
        [object]$Body = $null,
        [int]$TimeoutSec = 60
    )
    $headers = @{
        Authorization = "rest_api_key=$ApiKey"
        Accept        = 'application/json'
    }
    $params = @{
        Method      = $Method
        Uri         = $Url
        Headers     = $headers
        TimeoutSec  = $TimeoutSec
        ErrorAction = 'Stop'
    }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $params.SkipHeaderValidation = $true
        if ($SkipCertificateCheck) { $params.SkipCertificateCheck = $true }
    }
    if ($null -ne $Body) {
        $params.ContentType = 'application/json; charset=utf-8'
        $params.Body = if ($Body -is [string]) { $Body } else { ($Body | ConvertTo-Json -Compress -Depth 8) }
    }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = Invoke-WebRequest @params
        $sw.Stop()
        $parsed = $null
        if ($resp.Content) {
            try { $parsed = $resp.Content | ConvertFrom-Json } catch { $parsed = $null }
        }
        return [pscustomobject]@{
            Ok         = $true
            StatusCode = [int]$resp.StatusCode
            Url        = $Url
            Method     = $Method
            Ms         = $sw.ElapsedMilliseconds
            Body       = $resp.Content
            Json       = $parsed
            Error      = $null
        }
    }
    catch {
        $sw.Stop()
        $code = 0
        $text = $_.Exception.Message
        $respEx = $_.Exception.Response
        if ($respEx) {
            try { $code = [int]$respEx.StatusCode } catch { }
            try {
                $stream = $respEx.GetResponseStream()
                if ($stream) {
                    $reader = New-Object IO.StreamReader($stream)
                    $text = $reader.ReadToEnd()
                }
            }
            catch { }
        }
        if ($_.ErrorDetails.Message) { $text = $_.ErrorDetails.Message }
        $parsed = $null
        try { $parsed = $text | ConvertFrom-Json } catch { }
        return [pscustomobject]@{
            Ok         = $false
            StatusCode = $code
            Url        = $Url
            Method     = $Method
            Ms         = $sw.ElapsedMilliseconds
            Body       = $text
            Json       = $parsed
            Error      = $_.Exception.Message
        }
    }
}

function Get-IvantiMessage($Result) {
    $j = $Result.Json
    if ($null -eq $j) { return ($Result.Body | Out-String).Trim() }
    $m = $j.message
    if ($m -is [array]) { return ($m -join '; ') }
    if ($m) { return [string]$m }
    if ($j.description) { return [string]$j.description }
    if ($j.error) { return [string]$j.error }
    $raw = ($Result.Body | Out-String).Trim()
    if ($raw.Length -gt 240) { return $raw.Substring(0, 240) }
    return $raw
}

function Join-IvantiUrl([string]$Base, [string]$Rel) {
    return ($Base.TrimEnd('/') + '/' + $Rel.TrimStart('/'))
}

function Get-IvantiRoots([string]$InputUrl) {
    $u = $InputUrl.TrimEnd('/')
    $roots = New-Object System.Collections.Generic.List[string]
    foreach ($c in @($u, ($u -replace '/rest$', ''), ($u -replace '/HEAT/api/rest$', '/api'), ($u -replace '/HEAT/api/rest$', '/HEAT/api'), ($u -replace '/api/rest$', '/api'))) {
        if ($c -and -not $roots.Contains($c)) { $roots.Add($c) }
    }
    return $roots
}

function Get-IvantiRows($Json) {
    if ($null -eq $Json) { return @() }
    if ($Json.value) { return @($Json.value) }
    if ($Json.d -and $Json.d.results) { return @($Json.d.results) }
    if ($Json -is [array]) { return @($Json) }
    return @($Json)
}

New-IvantiDir $OutDir
$probeLog = Join-Path $OutDir 'probe-log.jsonl'
$reportPath = Join-Path $OutDir 'report.md'
if (Test-Path -LiteralPath $probeLog) { Remove-Item -LiteralPath $probeLog -Force }
$findings = New-Object System.Collections.Generic.List[object]

function Add-IvantiFinding($Name, $Result, $Note) {
    $item = [pscustomobject]@{
        Name       = $Name
        Ok         = [bool]$Result.Ok
        StatusCode = $Result.StatusCode
        Method     = $Result.Method
        Url        = $Result.Url
        Ms         = $Result.Ms
        Note       = $Note
        Message    = (Get-IvantiMessage $Result)
    }
    $findings.Add($item) | Out-Null
    $item | ConvertTo-Json -Compress | Add-Content -LiteralPath $probeLog -Encoding UTF8
    $mark = if ($Result.Ok) { 'OK ' } else { 'FAIL' }
    Write-Host ("  {0} {1,-5} {2}  {3}" -f $mark, $Result.StatusCode, $Name, $Note)
}

Write-IvantiStep "Проверка доступности хоста"
$uri = [Uri]$BaseUrl
Write-Host ("Хост: {0}" -f $uri.Host)
Write-Host ("База: {0}" -f $BaseUrl)
Write-Host ("Ключ: длина {0}, в файлы не пишется" -f $ApiKey.Length)
Write-Host ("Отчёт: {0}" -f $OutDir)

$roots = Get-IvantiRoots $BaseUrl
Write-Host ("Варианты корня API: {0}" -f ($roots -join ' | '))

Write-IvantiStep "Поиск рабочего корня API"
$aliveRoot = $null
$aliveKind = $null
$rootCandidates = @(
    @{ Rel = 'odata/businessobject/incidents?$top=1'; Kind = 'odata' },
    @{ Rel = 'incidents'; Kind = 'rest' },
    @{ Rel = 'odata/businessobject/incidents'; Kind = 'odata' }
)
foreach ($root in $roots) {
    foreach ($c in $rootCandidates) {
        $url = Join-IvantiUrl $root $c.Rel
        $r = Invoke-Ivanti -Url $url
        Add-IvantiFinding "root $($c.Kind) $root" $r $(if ($r.Ok) { 'живой корень' } else { (Get-IvantiMessage $r) })
        if ($r.Ok -and -not $aliveRoot) {
            $aliveRoot = $root
            $aliveKind = $c.Kind
        }
    }
}

if (-not $aliveRoot) {
    Write-Host "Ни один корень не ответил 2xx. Проверьте VPN, сертификат и ключ." -ForegroundColor Yellow
}

Write-IvantiStep "Чтение бизнес-объектов (права ключа)"
$objects = @(
    'incidents', 'Incident', 'servicereq', 'ServiceReq', 'services', 'Service',
    'category', 'Category', 'categories', 'ActualCategory', 'Frs_data_validation',
    'ValidationList', 'ci', 'Employee', 'Profile.Employee', 'Frs_CompositeContract_Contact',
    'Problem', 'Change', 'Journal'
)
$readable = New-Object System.Collections.Generic.List[string]
if ($aliveRoot) {
    foreach ($obj in $objects) {
        $urls = @(
            (Join-IvantiUrl $aliveRoot ("odata/businessobject/${obj}?`$top=1")),
            (Join-IvantiUrl $aliveRoot $obj)
        )
        foreach ($url in $urls) {
            $r = Invoke-Ivanti -Url $url
            $note = if ($r.Ok) { 'чтение разрешено' } else { (Get-IvantiMessage $r) }
            Add-IvantiFinding "read $obj" $r $note
            if ($r.Ok) {
                $readable.Add($obj) | Out-Null
                $safeName = ($obj -replace '[^\w\.-]', '_')
                $r.Body | Set-Content -LiteralPath (Join-Path $OutDir "sample-$safeName.json") -Encoding UTF8
                break
            }
        }
    }
}

Write-IvantiStep "Справочник категоризации (уникальные Service / Category / ActualCategory)"
$catRows = New-Object System.Collections.Generic.List[object]
if ($aliveRoot) {
    $select = 'Service,Service_Valid,Category,Category_Valid,ActualCategory,ActualCategory_Valid,CauseCode,CauseCode_Valid,Priority,Urgency,Impact,Status'
    $skip = 0
    $page = 100
    $maxPages = 30
    for ($i = 0; $i -lt $maxPages; $i++) {
        $url = Join-IvantiUrl $aliveRoot ("odata/businessobject/incidents?`$top=$page&`$skip=$skip&`$select=$select")
        $r = Invoke-Ivanti -Url $url
        Add-IvantiFinding "incidents page $i skip=$skip" $r $(if ($r.Ok) { "записей: $((Get-IvantiRows $r.Json).Count)" } else { (Get-IvantiMessage $r) })
        if (-not $r.Ok) { break }
        $batch = @(Get-IvantiRows $r.Json)
        if ($batch.Count -eq 0) { break }
        foreach ($row in $batch) { $catRows.Add($row) | Out-Null }
        if ($batch.Count -lt $page) { break }
        $skip += $page
    }
    if ($catRows.Count -eq 0) {
        $url = Join-IvantiUrl $aliveRoot 'incidents'
        $r = Invoke-Ivanti -Url $url
        Add-IvantiFinding 'incidents rest list' $r $(if ($r.Ok) { "записей: $((Get-IvantiRows $r.Json).Count)" } else { (Get-IvantiMessage $r) })
        foreach ($row in (Get-IvantiRows $r.Json)) { $catRows.Add($row) | Out-Null }
    }
}

$catalog = $catRows |
    ForEach-Object {
        [pscustomobject]@{
            Service              = $_.Service
            Service_Valid        = $_.Service_Valid
            Category             = $_.Category
            Category_Valid       = $_.Category_Valid
            ActualCategory       = $_.ActualCategory
            ActualCategory_Valid = $_.ActualCategory_Valid
            CauseCode            = $_.CauseCode
            Priority             = $_.Priority
            Urgency              = $_.Urgency
            Impact               = $_.Impact
            Status               = $_.Status
        }
    } |
    Where-Object { $_.Category -or $_.Service } |
    Sort-Object Service, Category, ActualCategory -Unique

$catalogPath = Join-Path $OutDir 'categorization.csv'
$catalog | Export-Csv -LiteralPath $catalogPath -NoTypeInformation -Encoding UTF8
$catalog | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $OutDir 'categorization.json') -Encoding UTF8

$svcCount = @($catalog | Select-Object -ExpandProperty Service -Unique | Where-Object { $_ }).Count
$catCount = @($catalog | Select-Object -ExpandProperty Category -Unique | Where-Object { $_ }).Count
$actCount = @($catalog | Select-Object -ExpandProperty ActualCategory -Unique | Where-Object { $_ }).Count

Write-Host ("Уникальных троек Service+Category+ActualCategory: {0}" -f @($catalog).Count)
Write-Host ("Сервисов: {0}; категорий: {1}; фактических категорий: {2}" -f $svcCount, $catCount, $actCount)

$writeResult = 'не проверялось (на проде по умолчанию безопасно; поставьте $TestWrite = $true если нужно)'
if ($TestWrite -and $aliveRoot) {
    Write-IvantiStep "Проба записи (заведомо невалидный POST, инцидент не должен создаться)"
    $payload = @{ Subject = 'API-PROBE-DO-NOT-KEEP'; Symptom = 'probe write permission' }
    $url = if ($aliveKind -eq 'odata') {
        Join-IvantiUrl $aliveRoot 'odata/businessobject/incidents'
    }
    else {
        Join-IvantiUrl $aliveRoot 'incidents'
    }
    $r = Invoke-Ivanti -Method POST -Url $url -Body $payload
    $msg = Get-IvantiMessage $r
    if ($r.StatusCode -in 401, 403) {
        $writeResult = "запись запрещена ($($r.StatusCode)): $msg"
    }
    elseif ($r.Ok -or $r.StatusCode -in 200, 201) {
        $writeResult = "ВНИМАНИЕ: сервер принял POST ($($r.StatusCode)). Проверьте, не создался ли инцидент, и удалите его."
    }
    else {
        $writeResult = "запись technically доступна, объект отклонён валидацией ($($r.StatusCode)): $msg"
    }
    Add-IvantiFinding 'POST incidents probe' $r $writeResult
}

Write-IvantiStep "Отчёт"
$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$readableText = if ($readable.Count) { ($readable | Select-Object -Unique) -join ', ' } else { 'ничего не прочитано' }
$authOk = [bool]$aliveRoot
$tripleCount = @($catalog).Count

$colleague = if ($tripleCount -gt 0) {
    "Доступ через API к категоризации инцидентов есть: выгружены уникальные сочетания Service / Category / ActualCategory ($tripleCount троек). Этого достаточно для сводного отчёта по заявкам. Отдельный CRUD-справочник может быть недоступен — в ISM категории живут как зависимые validation-списки."
}
elseif ($authOk) {
    "Ключ живой, но справочник категоризации из инцидентов снять не удалось (нет записей или поля называются иначе). Смотрите sample-*.json и probe-log.jsonl — какие объекты ключ всё же читает."
}
else {
    "Ключ или URL не сработали с этой машины. Нужны: доступ в контур svo.air.loc, корректный BaseUrl (часто рабочий корень /api, а не /HEAT/api/rest) и действующий rest_api_key."
}

$report = @"
# Проверка ключа Ivanti / HEAT (МАШ)

Дата: $now
Хост: $($uri.Host)
Переданный BaseUrl: $BaseUrl
Рабочий корень: $(if ($aliveRoot) { $aliveRoot } else { 'не найден' })
Стиль API: $(if ($aliveKind) { $aliveKind } else { 'не определён' })

## 1. Что можно делать с этим ключом

- Аутентификация: $(if ($authOk) { 'успешна (ключ живой)' } else { 'не прошла — ключ, путь или сеть' })
- Чтение объектов: $readableText
- Запись инцидентов: $writeResult

Ключ в Ivanti наследует права учётной записи, для которой он выпущен. Если чтение incidents прошло — коллеги могут забирать поля заявок, в том числе категоризацию. Если 401/403 — ключ недействителен или роли не хватает.

## 2. Справочник «Категоризация инцидентов»

В Ivanti отдельного плоского справочника категорий часто нет: Category зависит от Service, ActualCategory — от Category. Рабочий способ для отчёта — уникальные тройки из существующих инцидентов.

Снято записей инцидентов (пагинация top=100): $($catRows.Count)
Уникальных троек Service + Category + ActualCategory: $tripleCount
Сервисов: $svcCount
Категорий: $catCount
Фактических категорий: $actCount

Файлы в $OutDir :
- categorization.csv
- categorization.json
- probe-log.jsonl
- sample-*.json

## 3. Ответ коллегам (черновик)

$colleague
"@

$report | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Host ""
Write-Host "Отчёт: $reportPath"
Write-Host "CSV:   $catalogPath"
Write-Host "Лог:   $probeLog"
Write-Host ""
Write-Host $report
