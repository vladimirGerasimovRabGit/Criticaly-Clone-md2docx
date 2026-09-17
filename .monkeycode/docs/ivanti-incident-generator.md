# Генератор инцидентов Ivanti Service Manager

Дата: 2026-09-17  
Статус: прогон 10000 записей завершён (9836 успешно, 164 ошибки)

## Цель

Сгенерировать процедурно разные инциденты (`incidents`) в Ivanti Service Manager через REST API для обучения внутренней нейросети. Объект — именно `incidents`. Записи различаются по услуге, категории, срочности, влиянию, приоритету, команде исполнителя, источнику, причине, статусу и тексту.

## Итог прогона

| Показатель | Значение |
|---|---|
| Запрошено | 10000 |
| Успешно (уникальные индексы в `incidents_v2_state.json`) | **9836** |
| Ошибки | **164** |
| Время полного прогона (индексы 1000–9999 + повтор 70) | 2727 с (~45 мин) |
| Первый тест 1000 | 930 успешно / 70 ошибок / 489 с |
| Endpoint | `POST https://otbasybank-try.trysaasiteu.com/api/odata/businessobject/incidents` |
| Аутентификация | заголовок `Authorization: rest_api_key=<key>` |

Ошибки 164:

- 152 — статусы Closed / Resolved, сервер отвечает `DataLayer.PromptException` даже при заполненном `Resolution`. POST и последующий PUT/PATCH из Logged в Closed тоже отклоняются.
- 12 — прочие статусы.

Три профиля контакта, из‑за которых падали 56 записей в первой тысяче, отфильтрованы. После фильтрации ошибок `Attempting to link ... ProfileLink` не осталось.

## Что варьируется

Свободно (проверено на живом API, не зависят от пары service+category):

- Urgency — 3 значения, равномерно
- Impact — 3 значения, равномерно
- Priority — 5 значений, равномерно
- Source — 11 значений
- CauseCode — 11 значений
- ProfileLink — 69 из 72 профилей (3 отфильтрованы)
- Status — Logged / Active / Waiting for Resolution свободно; Closed / Resolved API отклоняет

Связаны и кроссовать нельзя:

- Service + Category + ActualCategory — 213 валидных троек из 19 сервисов и 46 категорий
- Owner + OwnerTeam — только пары, реально встречающиеся в той же группе service+category (78 групп, 208 пар)

Балансировка сервисов: round-robin по 19 сервисам, внутри сервиса — по категориям. На 10000 записей получается 526–527 на каждый сервис.

## Порядок работы

Из каталога `tools/`:

```bash
python3 build_reference.py --base-url https://otbasybank-try.trysaasiteu.com/api --out reference.json --insecure
python3 build_skeletons.py --base-url https://otbasybank-try.trysaasiteu.com/api --out skeletons.json --insecure
python3 build_combos.py --skeletons skeletons.json --out combos.json --variation variation.json
python3 build_owner_pool.py --skeletons skeletons.json --out owners.json
```

Полный запуск (resume через `incidents_v2_state.json`):

```bash
IVANTI_API_KEY=<key> python3 generate_incidents.py \
  --count 10000 \
  --base-url https://otbasybank-try.trysaasiteu.com/api \
  --skeletons skeletons.json \
  --combos combos.json \
  --variation variation.json \
  --owners owners.json \
  --reference reference.json \
  --push --insecure --concurrency 5 \
  --state incidents_v2_state.json \
  --dataset incidents_v2_dataset.jsonl \
  --failed incidents_v2_failed.ndjson
```

Dry-run без API: та же команда без `--push`.

## Файлы в `tools/`

| Файл | Назначение |
|---|---|
| `generate_incidents.py` | Генератор. По умолчанию dry-run, `--push` отправляет в API |
| `build_reference.py` | Справочник имя→GUID → `reference.json` |
| `build_skeletons.py` | Согласованные комбинации из существующих инцидентов → `skeletons.json` |
| `build_combos.py` | 213 уникальных троек service+category+actualcategory → `combos.json`, пул urgency/impact/priority/status → `variation.json` |
| `build_owner_pool.py` | Допустимые пары исполнитель/команда по группам → `owners.json` |
| `reference.json` | Справочник, 72 профиля, enumerations |
| `skeletons.json` | 549 скелетов из исходных инцидентов |
| `combos.json` | 213 валидных троек |
| `variation.json` | Пулы Urgency / Impact / Priority / Status |
| `owners.json` | 78 групп, 208 пар Owner/OwnerTeam |
| `incidents_v2_state.json` | Resume: 9836 успешных индексов |
| `incidents_v2_dataset.jsonl` | Успешно отправленные payload |
| `incidents_v2_failed.ndjson` | Лог ошибок (включая повторные попытки) |
| `incidents_dataset.jsonl` | Dry-run 10000 старого отчёта, в API не уходил целиком |

Суффикс `v2` обязателен: старый `incidents_state.json` относится к перекошенной схеме без балансировки сервисов.

## Ограничения инстанса

- `$top` больше 100 → `ISM_4000 You cannot query more than 100 records`. Читать постранично `$top=100&$skip=N`.
- `$metadata` недоступен (`ISM_4004 No service for type IEdmModel`).
- При `$skip` за пределами выборки тело ответа пустое, не JSON.
- Путь `/HEAT/api` даёт 404. Рабочая база: `https://<host>/api`.
- Поле `XER_Cloned` отсутствует. Генератор исключает неизвестные поля по regex `Field '([^']+)' was not found`.
- Category зависит от Service, ActualCategory — от Category. Чужие комбинации → `UndefinedValidatedValue ... is not in the validation list`.
- Owner зависит от OwnerTeam и от группы service+category.
- Closed/Resolved через REST не создаются и не переводятся из Logged. Logged / Active / Waiting for Resolution проходят.
- Три RecID профиля отклоняются при линковке `ProfileLink` и исключены из пула:
  `A3CC26C6687C42BF9E8501A90A02BD08`,
  `5D411FA1DCA7482588E505883B6A1610`,
  `4321CF5001A341F0B4254F4AB29B8831`.

## Тестовые записи, уже лежащие в системе

Исходных инцидентов инстанса (не создавались генератором): номера 10001–12380, 599 штук.

Созданные в ходе отладки (не удалялись):

- Диагностические PROBE — 68 шт. Номера 12403–12417 (PROBE2), 12453–12499 (PROBE4), 12506–12520 (PROBE5), 12533 (PROBE7).
- Пробные прогоны генератора — 38 шт. Номера 12382–12394, 12424–12435, 12534–12548.
- После этого — основная выгрузка v2 (~9836 записей).

Удаление не выполнялось.

## Git

На 2026-09-17 содержимое **не закоммичено**. Ветка `main`, последний коммит `255b0b7 Add files via upload`. Untracked: `tools/`, `.monkeycode/`, каталог `клонирование и оценка/`. Unstaged: удалён `workspace21.08.26.zip`. Коммит не создавался.
