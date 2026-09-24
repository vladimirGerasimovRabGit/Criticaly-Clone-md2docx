# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Format

### User Instruction Entry
[Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Scenario or timing mentioned]
- Instructions:
  - [Content of user teaching, line by line]

### Project Knowledge Entry
[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent during [task]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Knowledge points, line by line]

## Dedup Strategy
- Before adding a new entry, check for similar or identical instructions.
- If duplicate found, skip or merge with existing entry, updating date/context.
- Keep file under 150 lines by consolidating same-category rules.

## Entries

[User Instruction Summary]
- Date: 2026-09-15
- Context: Задача сгенерировать 10000 записей-инцидентов в Ivanti Service Manager через API для обучения внутренней нейросети
- Instructions:
  - Целевой объект — incidents (не ServiceReq и не CI)
  - Записи должны быть процедурно разными (разные тексты, категории, сервисы, приоритеты), а не клонами одного образца
  - API-ключи и параметры подключения передаются пользователем в чат; не выводить реальные значения ключей в ответах

[Project Knowledge Summary]
- Date: 2026-09-15
- Context: Discovered by Agent while building an incident generator against the Ivanti instance
- Category: Operations & Deployment
- Instructions:
  - Base URL инстанса: https://otbasybank-try.trysaasiteu.com
  - OData-база для API: https://<host>/api (НЕ /HEAT/api — тот отдаёт 404 с HTML-заглушкой)
  - Endpoint создания инцидента: POST /api/odata/businessobject/incidents
  - Endpoint чтения: GET /api/odata/businessobject/incidents
  - Аутентификация: заголовок Authorization: rest_api_key=<key>

[Project Knowledge Summary]
- Date: 2026-09-15
- Context: Discovered by Agent while querying the Ivanti OData API
- Category: Troubleshooting & Debugging
- Instructions:
  - Жёсткий лимит: запрос с $top больше 100 возвращает ISM_4000 "You cannot query more than 100 records"; собирать данные постранично $top=100&$skip=N
  - $metadata и $count могут быть недоступны (ISM_4004 "No service for type IEdmModel")
  - Поля с суффиксом _Valid содержат GUID зависимых справочников; имя рядом (без _Valid) — человекочитаемое значение
  - Ivanti проверяет зависимые validation-списки: ActualCategory зависит от Category/Service, Owner — от OwnerTeam, ProfileLink — от типа контакта (Frs_CompositeContract_Contact). Произвольная комбинация GUID отклоняется с "not in the validation list"
  - Надёжный способ получить валидные комбинации — взять существующие инциденты и переиспользовать их согласованные наборы (скелеты)
  - Поле XER_Cloned отсутствует в этой инсталляции — POST с ним даёт "Field 'XER_Cloned' was not found"
  - В данных могут быть повреждённые GUID (обрывок + пробел); валидировать длину/формат 32 hex перед отправкой
  - Files_Valid/HoursOfOperation: в инстансе одно значение "Weekly HOP"

[Project Knowledge Summary]
- Date: 2026-09-15
- Context: Discovered by Agent while implementing the generator toolchain
- Category: Build Methods
- Instructions:
  - Инструменты лежат в /workspace/tools (Python 3.11, только stdlib + requests)
  - build_reference.py — снимает с инстанса справочник имя→GUID и сохраняет reference.json
  - build_skeletons.py — собирает согласованные комбинации (скелеты) из реальных инцидентов
  - generate_incidents.py — генератор: по умолчанию dry-run в incidents_dataset.jsonl, с --push отправляет в API
  - Запуск отправки: IVANTI_API_KEY=<key> python3 generate_incidents.py --count N --base-url <url> --reference reference.json --push --insecure --concurrency 4 --rate 8
  - Поддерживается resume через incidents_state.json и лог ошибок incidents_failed.ndjson
  - В этой локальной среде прямой доступ к внешнему Ivanti есть (curl с -k), интернет доступен

[Project Knowledge Summary]
- Date: 2026-09-15
- Context: Discovered by Agent while making bulk incident creation pass Ivanti validation
- Category: Troubleshooting & Debugging
- Instructions:
  - Произвольная подстановка GUID в *_Valid отклоняется: "UndefinedValidatedValue ... is not in the validation list", а также ошибки связей ProfileLink -> Frs_CompositeContract_Contact
  - Рабочее решение: собирать «скелеты» — согласованные наборы Valid-значений из уже существующих инцидентов (build_skeletons.py, 600 инцидентов -> 549 уникальных комбинаций), и переиспользовать их целиком
  - Пагинация OData: при $skip за пределами выборки сервер отдаёт пустой ответ вместо JSON — парсер должен возвращать {} на пустое тело
  - Проверка результата: POST 10 записей со скелетами прошёл 10/10 (в базе стало 610; номера 12382–12394)
  - Порядок работы: build_reference.py (имена) -> build_skeletons.py (согласованные комбинации) -> generate_incidents.py --skeletons skeletons.json
  - Текст Subject/Symptom генерируется по названию категории через TEXT_BY_CATEGORY; есть общий fallback для неизвестных категорий

[Project Knowledge Summary]
- Date: 2026-09-15
- Context: Discovered by Agent while varying OwnerTeam (команды) in generated incidents
- Category: Troubleshooting & Debugging
- Instructions:
  - Owner/OwnerTeam валидируются в контексте группы service+category: одна и та же пара проходит в одной группе и отклоняется в другой
  - Перекрёстные пары исполнитель/команда (например Ron.Thomas/IT) отклоняются с "not in the validation list" — использовать только пары, реально встречающиеся в данных для той же группы
  - Рабочее решение: build_owner_pool.py группирует допустимые пары по service+category (78 групп, 208 пар) в owners.json
  - Балансировка команд: в IncidentFactory._pick_owner выбирается наименее использованная команда среди доступных в группе; 10000 записей дают Service Desk ~52%, остальные распределены (Network Support, Application Development, Desktop Support, IT, Change Management, Operations)
  - Доля не-Service Desk ограничена объёмом данных инстанса: большинство групп содержат только пары с Service Desk
  - Итоговая проверка: 12 записей с балансировкой команд прошли 12/12
  - Порядок работы обновлён: build_reference.py -> build_skeletons.py -> build_owner_pool.py -> generate_incidents.py --skeletons --owners

[User Instruction Summary]
- Date: 2026-09-16
- Context: Уточнение к генератору — поле "команды" понято как OwnerTeam, дополнительно требуется разнообразие услуг, категорий, срочности и влияния
- Instructions:
  - Услуги (Service), категории (Category), срочность (Urgency), влияние (Impact) должны быть разными, а не сведены к одному-двум значениям

[Project Knowledge Summary]
- Date: 2026-09-16
- Context: Discovered by Agent while decoupling varied fields from the validated combination (probes against live API)
- Category: Troubleshooting & Debugging
- Instructions:
  - Category жёстко зависит от Service: чужая категория при своём сервисе даёт UndefinedValidatedValue "not in the validation list"; кроссовать сервис и категорию нельзя
  - ActualCategory тоже нельзя подставлять чужую: на чужую категорию сервер отвечает Required field Incident.Summary (поля Summary в объекте нет), свою — принимает
  - Свободно варьируются (не зависят от пары service+category): CauseCode_Valid (11 значений), Source_Valid (11), Urgency_Valid x Impact_Valid (все 9 пар), Priority_Valid (все 8 значений, включая русские "Средний/Высокий/Критичный"), ProfileLink_RecID (72 профиля)
  - Рабочее решение: build_combos.py собирает 213 уникальных валидных троек (service+category+actualcategory) из скелетов и выносит urgency/impact/priority/status в variation.json
  - Балансировка сервисов: IncidentFactory._pick_base чередует сервисы по кругу (индекс % число сервисов), внутри сервиса — категории по кругу; 10000 записей дают ровно 526-527 на каждый из 19 сервисов и 46 категорий
  - Итоговая проверка: 15 записей с вариацией дошли 15/15; в системе подтверждены разные Urgency/Impact/Priority/Source/Status
  - Порядок работы: build_reference.py -> build_skeletons.py -> build_combos.py -> build_owner_pool.py -> generate_incidents.py --skeletons --combos --variation --owners
  - resume-файл incidents_state.json привязан к схеме генерации: после смены схемы для консистентного датасета нужно указывать новый --state

[Project Knowledge Summary]
- Date: 2026-09-18
- Context: Discovered by Agent after v2 push and Closed/Resolved 10000-run
- Category: Operations & Deployment
- Instructions:
  - v2 (Logged/Active/Waiting): 9836/10000; resume `incidents_v2_state.json` / `incidents_v2_dataset.jsonl` / `incidents_v2_failed.ndjson`
  - Closed/Resolved: 10000/10000; resume `incidents_closed_state.json` / `incidents_closed_dataset.jsonl` / `incidents_closed_failed.ndjson`; seed `20260918`
  - Команда закрытых (из /workspace/tools): `IVANTI_API_KEY=<key> python3 generate_incidents.py --count 10000 --base-url https://otbasybank-try.trysaasiteu.com/api --skeletons skeletons.json --combos combos.json --variation variation.json --owners owners.json --reference reference.json --push --insecure --concurrency 5 --status Closed --status Resolved --seed 20260918 --state incidents_closed_state.json --dataset incidents_closed_dataset.jsonl --failed incidents_closed_failed.ndjson`
  - Closed/Resolved через POST принимают при заполненных отображаемых `CauseCode` + `Resolution`; одного `CauseCode_Valid` недостаточно (NotEmpty CauseCode)
  - Source=Chat (`2C6B9DDD886D4C25B7F194614FFCBBBB`) на Closed/Resolved даёт `DataLayer.PromptException`; генератор исключает этот Source
  - Пара Owner=Admin / OwnerTeam=Operations (`FB884D18...` / `430F01AC...`) отклоняется (`UndefinedValidatedValue Owner`); тройки, где это единственная пара, выкидываются из пула combos
  - `--status Closed --status Resolved` ограничивает `variation.statuses`
  - Три RecID профиля исключены: A3CC26C6687C42BF9E8501A90A02BD08, 5D411FA1DCA7482588E505883B6A1610, 4321CF5001A341F0B4254F4AB29B8831

[Project Knowledge Summary]
- Date: 2026-09-24
- Context: Discovered by Agent while restoring browser clicks (MonkeyCode 26091601.0.0 broke browser_snapshot)
- Category: Environment Configuration
- Instructions:
  - В сборке MonkeyCode 26091601.0.0 сломан browser_snapshot (парсер ждёт массив, приходит строка) — browser_click/browser_type/browser_select_option не работают ни на одной странице
  - Обход: tools/mc-bridge/bridge.py встаёт на место "agent" расширения MonkeyCode и отдаёт HTTP-API к CDP (порт 8791); подробности — AdditionalBridgeToClickandOther.md в корне репозитория
  - Запуск: python bridge.py; в настройках расширения MonkeyCode указать agent 端口 = 8791; вернуть приложению — очистить поле порта
  - Умеет: клики (одиночный/двойной/тройной), выделение протяжкой и программно, ввод текста, копирование/вставка через системный буфер, клавиши, скриншоты, вкладки, произвольный JS в странице
  - Ctrl+C/Ctrl+V внедрёнными через отладчик нажатиями не срабатывают — нужен параметр commands у Input.dispatchKeyEvent (в мосте точка /edit)
  - Самопроверка: tools/mc-bridge/selftest.ps1 — создаёт тестовую вкладку, проверяет все возможности и закрывает её
  - Токены приложения в файлах заменены на плейсхолдеры; реальные значения хранятся локально и в репозиторий не попадают

[User Instruction Summary]
- Date: 2026-09-24
- Context: Требование к языку общения (проект VPN-обхода блокировок)
- Instructions:
  - Все ответы и пояснения пользователю — на русском языке

[Project Knowledge Summary]
- Date: 2026-09-24
- Context: Discovered by Agent while building a censorship-resistant VPN (RU user) on VPS 85.209.155.32
- Category: Operations & Deployment
- Instructions:
  - VPS: UFO.Hosting Haedus[FI] 85.209.155.32; SSH host-алиас `awg` (/tmp/opencode/awg_ssh_config, ключ /tmp/opencode/awg_deploy); sshd/fail2ban троттлит — при `kex_exchange_identification: Connection closed`/`scp: Connection closed` подождать 5–10 мин, не чаще 1 попытки/5 сек
  - Docker: amnezia-awg (51820/udp), xray-ss (8388, SS-2022), mtg (9443); amnezia-xray Reality остановлен (443 занят); Hysteria2 (443) и SS-2022 direct — ТСПУ душит; VPS без IPv6; *.workers.dev и *.trycloudflare.com глушатся ТСПУ по имени хоста
  - Рабочая схема — ShadowTLS v3 на VPS: sing-box 1.14.2 (/usr/local/bin/sing-box), inbound shadowtls 0.0.0.0:443 (handshake www.bing.com:443) → detour на inbound shadowsocks 127.0.0.1:18388 (2022-blake3-aes-128-gcm); unit `sing-box-shadowtls.service`, конфиг /etc/sing-box/st_server.json (секреты — в конфиге, в чат не выводить)
  - КРИТИЧНО: Rust shadow-tls v0.2.25 сервер НЕ совместим с sing-box-клиентом на v3-handshake (HMAC-схема расходится) — сервером обязан быть sing-box; Rust-клиент работает, годится только для диагностики
  - КРИТИЧНО: strict_mode=false — в sing-shadowtls v0.2.1 парсер isServerHelloSupportTLS13 сломан; при strict_mode:true сервер уходит в plain-relay и клиент падает с `shadow-tls v3: hmac mismatch, possible data corruption`; non-strict стабилен (30/30 локально)
  - Проверка маскировки: `openssl s_client -connect 85.209.155.32:443 -servername www.bing.com` → реальный сертификат Microsoft r.bing.com
  - Клиентский профиль (sing-box JSON для Hiddify): outbound shadowsocks (server=VPS:443, password=SS-2022, detour=shadowtls) + outbound shadowtls (version 3, password, tls.server_name=www.bing.com, utls chrome); готовый файл — /workspace/ft-shadowtls.json; проверка: sing-box client + `curl --socks5-hostname` → api.ipify.org = 85.209.155.32; с песочницы 12/12 (ip+YouTube 200), но в первые ~10 с после рестарта сервера возможны ложные `hmac mismatch` — просто повторить
  - Cloudflare не помог: воркер ftvpn (VLESS-over-WS) глушится по имени; CF-токен без прав Pages; бесплатный домен DigitalPlat блокируется KYC (лимит 1 домен); деплой воркера — multipart с filename=main_module
  - Диагностика ShadowTLS: /tmp/opencode/capture.py (перехват ClientHello) + analyze.py (воспроизведение проверки HMAC сервера); Rust-клиент `shadow-tls --v3 client --server <локальный слушатель>` для сравнения
