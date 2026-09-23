# Проверка ключа Ivanti / HEAT (МАШ)

Запуск **вставкой в консоль**. Файл сохранять не нужно.

Инстанс: `https://svoivantiprod1.svo.air.loc/HEAT/api/rest/` (доступен только из контура МАШ).

## Как запустить

1. Откройте `tools/mash/Probe-IvantiKey.ps1`.
2. В первых строках поставьте ключ:

```powershell
$ApiKey = 'ваш-ключ'
$BaseUrl = 'https://svoivantiprod1.svo.air.loc/HEAT/api/rest/'
$SkipCertificateCheck = $true
$TestWrite = $false
```

3. Скопируйте **весь** текст скрипта.
4. Вставьте в окно PowerShell и нажмите Enter.

Отчёт пишется в `Документы\ivanti-probe-out` — даже если консоль открыта из `C:\Windows\system32`.

`$TestWrite` оставьте `$false` на проде.

## Что получите

| Файл | Содержание |
|---|---|
| `report.md` | Жив ли ключ, что читает, черновик ответа коллегам |
| `categorization.csv` | Уникальные тройки Service / Category / ActualCategory |
| `categorization.json` | То же в JSON |
| `probe-log.jsonl` | Каждый HTTP-запрос |
| `sample-*.json` | Сырой ответ по доступным объектам |

После прогона пришлите `report.md` (без ключа).
