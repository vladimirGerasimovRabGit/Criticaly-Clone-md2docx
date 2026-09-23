# Вставьте весь текст в PowerShell. Ключ только в строке ниже.

$ApiKey = 'ВСТАВЬТЕ-КЛЮЧ'
$SkipCertificateCheck = $true

$ServiceUrl  = 'https://svoivantiprod1.svo.air.loc/HEAT/api/odata/businessobject/services?$top=1'
$CategoryUrl = 'https://svoivantiprod1.svo.air.loc/HEAT/api/odata/businessobject/categorys?$top=1'

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($SkipCertificateCheck -and $PSVersionTable.PSVersion.Major -lt 6) {
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
}

function Get-Ivanti($Url) {
    $headers = @{
        Authorization = "rest_api_key=$ApiKey"
        Accept        = 'application/json'
    }
    $p = @{
        Method      = 'GET'
        Uri         = $Url
        Headers     = $headers
        TimeoutSec  = 60
        ErrorAction = 'Stop'
    }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $p.SkipHeaderValidation = $true
        if ($SkipCertificateCheck) { $p.SkipCertificateCheck = $true }
    }
    try {
        $resp = Invoke-WebRequest @p
        Write-Host ""
        Write-Host ("OK  {0}  {1}" -f [int]$resp.StatusCode, $Url) -ForegroundColor Green
        if ($resp.Content) { Write-Host $resp.Content } else { Write-Host '(пустое тело)' }
    }
    catch {
        $code = 0
        $text = $_.Exception.Message
        if ($_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        }
        if ($_.ErrorDetails.Message) { $text = $_.ErrorDetails.Message }
        Write-Host ""
        Write-Host ("FAIL {0}  {1}" -f $code, $Url) -ForegroundColor Yellow
        Write-Host $text
    }
}

Write-Host 'Сервис:'
Get-Ivanti $ServiceUrl

Write-Host ''
Write-Host 'Категория (categorys):'
Get-Ivanti $CategoryUrl
