#!/usr/bin/env python3
"""Процедурный генератор инцидентов Ivanti Service Manager / HEAT (OData API).

Использует согласованные комбинации Valid-значений (скелеты), собранные
скриптом build_skeletons.py из существующих инцидентов, — это гарантирует
прохождение зависимых validation-списков Ivanti (Category↔Service↔
ActualCategory, Owner↔OwnerTeam, ProfileLink и т.д.).

Режимы:
  dry-run (по умолчанию)  -> сформировать incidents_dataset.jsonl без вызовов API
  --push                  -> отправлять POST /odata/businessobject/incidents

Примеры:
  python3 generate_incidents.py --count 10 --skeletons skeletons.json
  IVANTI_API_KEY=... python3 generate_incidents.py --count 10000 \
      --base-url https://host/api --skeletons skeletons.json --push \
      --concurrency 4 --rate 8 --insecure
"""

import argparse
import collections
import json
import os
import random
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

FIRST_NAMES = [
    "Александр", "Дмитрий", "Сергей", "Андрей", "Алексей", "Максим", "Иван",
    "Елена", "Ольга", "Наталья", "Анна", "Мария", "Татьяна", "Ирина",
    "Екатерина", "Павел", "Артём", "Виктор", "Роман", "Николай", "Юлия",
    "Светлана", "Ксения", "Дарья", "Михаил", "Владимир", "Олег", "Кирилл",
    "Karen", "David", "Jacob", "Summer", "Evelyn", "Alan", "Andrew", "Bryce",
]
LAST_NAMES = [
    "Иванов", "Петров", "Смирнов", "Кузнецов", "Соколов", "Попов", "Лебедев",
    "Козлов", "Новиков", "Морозов", "Волков", "Соловьёв", "Васильев",
    "Зайцев", "Павлов", "Семёнов", "Голубев", "Виноградов", "Богданов",
    "Davidson", "Clerk", "Taylor", "Smythe", "Sullivan", "Morris", "Street",
    "Swift", "Kirk", "Matthews", "Thomas",
]
DEPARTMENTS = [
    "Бухгалтерия", "Отдел кадров", "Отдел продаж", "Логистика", "Финансовый отдел",
    "Юридический отдел", "Служба поддержки", "Аналитический отдел", "Отдел разработки",
    "Маркетинг", "Склад", "Технический отдел", "Клиентский сервис", "Операционный отдел",
]
LOCATIONS = [
    "Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург",
    "Нижний Новгород", "Самара", "Челябинск", "Ростов-на-Дону", "Уфа",
    "Красноярск", "Воронеж", "Пермь", "Волгоград", "Алматы", "Астана",
]
ASSETS = [
    "ноутбук Lenovo ThinkPad", "ноутбук Dell Latitude", "ноутбук HP EliteBook",
    "принтер HP LaserJet", "МФУ Kyocera", "монитор Dell", "системный блок",
    "IP-телефон Yealink", "сканер Canon", "ИБП APC", "точка доступа Wi-Fi",
]
HOSTS = ["PC-{n:04d}", "NB-{n:04d}", "WS-{n:04d}", "TSP-{n:03d}", "PRN-{n:03d}"]
ERROR_CODES = [
    "0x80070005", "0x0000007B", "ERR_CONNECTION_REFUSED", "ORA-12154",
    "HTTP 500", "HTTP 403", "0xC0000098", "ERR_NAME_NOT_RESOLVED",
    "SQLSTATE 08001", "0x80004005", "NET::ERR_CERT_DATE_INVALID",
]
SOURCES = ["Телефон", "Электронная почта", "Портал самообслуживания", "Чат",
           "Личное обращение", "Мониторинг сети"]
APPS = ["1С:Предприятие", "SAP ERP", "Microsoft Outlook", "Microsoft Excel",
        "CRM-система", "Jira", "Confluence", "VPN-клиент", "СЭД", "АБИС Colvir",
        "система кадрового учёта", "портал самообслуживания"]


def group(subjects, symptoms):
    return {"subjects": subjects, "symptoms": symptoms}


GENERIC_REPORT = "Пользователь {fio} ({dept}) сообщает: {detail}. Локация {loc}, рабочее место {host}, ошибка {err}."

# Текстовые шаблоны по названию категории инцидента
TEXT_BY_CATEGORY = {
    "Account Lockout": group(
        ["Заблокирована учётная запись", "Не работает вход в систему", "Сброс пароля пользователя"],
        ["Учётная запись пользователя {fio} заблокирована. Хост {host}, {loc}, ошибка {err}.",
         "После неудачных попыток входа учётная запись {fio} заблокирована. Подразделение {dept}, требуется разблокировка.",
         "Пользователь {fio} запрашивает сброс пароля для {app}. Локация {loc}."]),
    "Application Error": group(
        ["Ошибка в бизнес-приложении", "Не формируется отчёт", "Сбой при выполнении операции"],
        ["При работе в {app} возникает ошибка {err}. Пользователь {fio}, {dept}, хост {host}.",
         "У пользователя {fio} не формируется отчёт в {app}, выводится {err}. Требуется анализ журналов.",
         "Операция в {app} завершается ошибкой {err}. Контакт {fio}, {loc}."]),
    "Application Failure": group(
        ["Приложение не запускается", "Отказ бизнес-приложения", "Приложение аварийно завершается"],
        ["{app} перестало запускаться у пользователя {fio}. Хост {host}, ошибка {err}.",
         "Аварийное завершение {app} при открытии документа. Подразделение {dept}, {loc}.",
         "Приложение {app} недоступно с {time}. Рабочее место {host}, ошибка {err}."]),
    "Application Security": group(
        ["Подозрительная активность в приложении", "Нарушение доступа в системе", "Подозрительный вход в систему"],
        ["Зафиксирован вход в {app} пользователя {fio} из нетипичного места. Локация {loc}.",
         "В {app} у пользователя {fio} обнаружена подозрительная активность. Требуется проверка ИБ, {dept}.",
         "Средство защиты зафиксировало инцидент доступа в {app}. Хост {host}, ошибка {err}."]),
    "Backup": group(
        ["Не выполнено резервное копирование", "Ошибка задания бэкапа", "Сбой архивации данных"],
        ["Задание резервного копирования не завершилось, ошибка {err}. Хост {host}, локация {loc}.",
         "Бэкап системы {host} завершился с ошибкой {err}. Требуется повторный запуск, {dept}.",
         "Не создана резервная копия данных за {time}. Контакт {fio}, {loc}."]),
    "Benefits Management": group(
        ["Ошибка в системе льгот", "Не отображаются выплаты", "Сбой расчёта компенсаций"],
        ["Пользователь {fio} ({dept}) не видит данные по компенсациям в {app}. Ошибка {err}.",
         "Расчёт выплат в {app} не формируется. Хост {host}, локация {loc}.",
         "Ошибка в модуле льгот системы {app}. Контакт {fio}, {dept}."]),
    "Cable Failure": group(
        ["Повреждён сетевой кабель", "Обрыв кабеля", "Нет связи по кабельной линии"],
        ["Обрыв сетевого кабеля на рабочем месте пользователя {fio}. Локация {loc}, хост {host}.",
         "Кабельная линия повреждена, связь отсутствует. Подразделение {dept}, контакт {fio}.",
         "Нет линка на порту, ошибка {err}. Требуется замена кабеля, {loc}."]),
    "Capacity": group(
        ["Недостаточно ёмкости хранилища", "Превышен лимит ресурсов", "Закончилось место"],
        ["Хранилище системы {host} заполнено, требуется расширение. Локация {loc}, ошибка {err}.",
         "Превышен лимит выделенных ресурсов для {app}. Пользователь {fio}, {dept}.",
         "Свободное место на {host} ниже критического уровня. Контакт {fio}, {loc}."]),
    "Client Failure": group(
        ["Неисправен рабочий компьютер", "Не включается компьютер", "Отказ рабочего места"],
        ["Не включается {asset} на рабочем месте пользователя {fio}, {loc}. Индикация отсутствует.",
         "Рабочая станция {host} не загружается. Подразделение {dept}, ошибка {err}.",
         "Отказ рабочего места {host}, работа пользователя {fio} остановлена."]),
    "Computer Provisioning": group(
        ["Требуется новое рабочее место", "Подготовка компьютера для сотрудника", "Выдача оборудования"],
        ["Требуется подготовить рабочее место для нового сотрудника {fio}, подразделение {dept}, локация {loc}.",
         "Запрос на выдачу {asset} пользователю {fio}. Локация {loc}.",
         "Необходимо настроить {host} для работы сотрудника {fio}, {dept}."]),
    "Connection Failure": group(
        ["Нет подключения к сети", "Обрыв соединения", "Не устанавливается соединение"],
        ["Пользователь {fio} ({dept}) не может подключиться к корпоративной сети. Хост {host}, ошибка {err}.",
         "Соединение с {app} обрывается с {time}. Рабочее место {host}, локация {loc}.",
         "Не устанавливается соединение с сетевым ресурсом. Контакт {fio}, ошибка {err}."]),
    "Connectivity": group(
        ["Проблемы с подключением", "Пропадает связь", "Медленная сеть"],
        ["С {time} наблюдаются разрывы соединения при работе на {host}. Локация {loc}, диагностика показывает {err}.",
         "Пользователь {fio} ({dept}) жалуется на нестабильное подключение к {app}. Хост {host}.",
         "Сетевой ресурс периодически недоступен для пользователя {fio}. Ошибка {err}, {loc}."]),
    "Corrupt Mailbox": group(
        ["Повреждён почтовый ящик", "Ошибка синхронизации почты", "Почта не открывается"],
        ["Почтовый ящик пользователя {fio} повреждён, синхронизация не выполняется. Хост {host}, ошибка {err}.",
         "Не открывается почта у пользователя {fio}. Подразделение {dept}, локация {loc}.",
         "Ошибка целостности ящика {fio}, требуется переиндексация. Ошибка {err}."]),
    "Data Corruption": group(
        ["Повреждение данных", "Данные повреждены", "Ошибка целостности данных"],
        ["Обнаружено повреждение данных в {app}. Требуется восстановление, контакт {fio}, {loc}.",
         "Данные в системе повреждены, ошибка {err}. Хост {host}, подразделение {dept}.",
         "Нарушена целостность записей в {app}. Пользователь {fio}, требуется проверка."]),
    "Data Loss": group(
        ["Потеря данных", "Пропали данные", "Нет данных в системе"],
        ["Пользователь {fio} ({dept}) сообщает о пропаже данных в {app}. Ошибка {err}, хост {host}.",
         "Данные за период не найдены в {app}. Требуется восстановление из бэкапа, {loc}.",
         "Потеря документов в {app} у пользователя {fio}. Контакт {dept}, локация {loc}."]),
    "Delivery Failure": group(
        ["Не доставляется почта", "Письма не доходят", "Ошибка доставки сообщений"],
        ["Сообщения не доставляются адресату, сервер возвращает {err}. Отправитель {fio}, {dept}.",
         "Пользователь {fio} не получает входящие письма с {time}. Хост {host}, локация {loc}.",
         "Очередь доставки почты переполнена, ошибка {err}. Контакт {fio}, {dept}."]),
    "Demo Category": group(
        ["Демонстрационное обращение", "Тестовый инцидент", "Пример обращения"],
        ["Демонстрационное обращение пользователя {fio}. Подразделение {dept}, локация {loc}, хост {host}.",
         "Тестовый инцидент для проверки процессов. Контакт {fio}, ошибка {err}.",
         "Пример обращения в службу поддержки. Пользователь {fio}, {dept}."]),
    "Desktop Hardware": group(
        ["Неисправно оборудование", "Отказ комплектующей", "Требуется замена оборудования"],
        ["Неисправно {asset} на рабочем месте пользователя {fio}, {loc}. Ошибка {err}.",
         "Отказ комплектующей в системе {host}. Подразделение {dept}, контакт {fio}.",
         "Требуется замена {asset}. Локация {loc}, пользователь {fio}."]),
    "Desktop Software": group(
        ["Ошибка в приложении", "Не работает программа", "Сбой после обновления"],
        ["После обновления {app} перестало работать у пользователя {fio}. Хост {host}, ошибка {err}.",
         "Приложение {app} не запускается на {host}. Подразделение {dept}, локация {loc}.",
         "Сбой в работе {app} у пользователя {fio}, ошибка {err}."]),
    "Device Failure": group(
        ["Отказ устройства", "Неисправно устройство", "Устройство не определяется"],
        ["Устройство {asset} не определяется системой {host}. Пользователь {fio}, ошибка {err}.",
         "Отказ устройства на рабочем месте {fio}, локация {loc}. Требуется замена.",
         "Неисправно {asset}, работа пользователя {fio} затруднена. Подразделение {dept}."]),
    "Enterprise Application Service": group(
        ["Ошибка корпоративной системы", "Недоступна бизнес-система", "Сбой корпоративного приложения"],
        ["Корпоративная система {app} недоступна с {time}. Пользователь {fio}, ошибка {err}, хост {host}.",
         "Сбой в {app} у пользователя {fio}, подразделение {dept}. Требуется диагностика.",
         "Операция в {app} не выполняется, ошибка {err}. Локация {loc}, контакт {fio}."]),
    "Error Message": group(
        ["Появляется сообщение об ошибке", "Непонятная ошибка в системе", "Ошибка при работе"],
        ["При работе в {app} появляется сообщение об ошибке {err}. Пользователь {fio}, хост {host}.",
         "Пользователь {fio} ({dept}) видит ошибку {err} при выполнении операции в {app}.",
         "Система выводит ошибку {err} на рабочем месте {host}, локация {loc}."]),
    "Facility Maintenance": group(
        ["Неисправность в помещении", "Требуется ремонт", "Проблема с инфраструктурой"],
        ["В помещении {loc} требуется ремонт. Контакт {fio}, подразделение {dept}.",
         "Неисправность инфраструктуры на объекте {loc}. Ошибка {err}, контакт {fio}.",
         "Требуется обслуживание помещения, заявка от {fio}. Локация {loc}."]),
    "Facility Safety": group(
        ["Нарушение безопасности в помещении", "Вопрос по охране труда", "Опасная ситуация"],
        ["Зафиксировано нарушение безопасности в помещении {loc}. Контакт {fio}, {dept}.",
         "Пользователь {fio} сообщает об опасной ситуации на объекте {loc}.",
         "Требуется проверка безопасности помещения. Подразделение {dept}, контакт {fio}."]),
    "Facility Security": group(
        ["Вопрос физической безопасности", "Нарушение доступа в помещение", "Инцидент охраны"],
        ["Зафиксирован несанкционированный доступ в помещение {loc}. Контакт {fio}, {dept}.",
         "Пользователь {fio} сообщает о нарушении охраны на объекте {loc}.",
         "Требуется проверка системы контроля доступа. Контакт {fio}, ошибка {err}."]),
    "Functionality": group(
        ["Не работает функция системы", "Отсутствует функциональность", "Ошибка в работе системы"],
        ["В {app} не работает требуемая функция у пользователя {fio}. Хост {host}, ошибка {err}.",
         "Функциональность {app} не соответствует ожиданиям. Подразделение {dept}, контакт {fio}.",
         "Операция в {app} выполняется некорректно, ошибка {err}. Локация {loc}."]),
    "Hard Drive Failure": group(
        ["Отказ жёсткого диска", "Диск не определяется", "Ошибка накопителя"],
        ["Отказ накопителя в системе {host}. Пользователь {fio}, ошибка {err}, локация {loc}.",
         "Жёсткий диск не определяется, данные недоступны. Подразделение {dept}, контакт {fio}.",
         "Ошибка чтения с накопителя {host}, требуется замена. Ошибка {err}."]),
    "Hardware": group(
        ["Неисправно оборудование", "Отказ оборудования", "Проблема с устройством"],
        ["Неисправно {asset} на рабочем месте пользователя {fio}, {loc}. Ошибка {err}.",
         "Отказ оборудования в системе {host}. Подразделение {dept}, контакт {fio}.",
         "Требуется диагностика {asset}. Локация {loc}, пользователь {fio}."]),
    "How-To": group(
        ["Консультация по работе с системой", "Как выполнить операцию", "Вопрос по использованию ПО"],
        ["Пользователь {fio} ({dept}) просит консультацию по работе с {app}. Локация {loc}.",
         "Требуется инструкция по выполнению операции в {app}. Контакт {fio}, хост {host}.",
         "Вопрос по использованию функциональности {app}. Обращение через «{source}»."]),
    "Incorrect Address Book": group(
        ["Ошибка в адресной книге", "Некорректные контакты", "Не отображаются адреса"],
        ["В адресной книге пользователя {fio} некорректные записи. Хост {host}, ошибка {err}.",
         "Не отображаются адреса получателей у пользователя {fio}. Подразделение {dept}, {loc}.",
         "Ошибка синхронизации адресной книги. Контакт {fio}, ошибка {err}."]),
    "Internet Failure": group(
        ["Нет доступа в интернет", "Пропадает интернет", "Не открываются внешние сайты"],
        ["Пользователь {fio} ({dept}) сообщает об отсутствии доступа в интернет. Хост {host}, ошибка {err}.",
         "Внешние ресурсы не открываются с {time}. Локация {loc}, диагностика показывает {err}.",
         "Пропадает доступ в интернет на рабочем месте {host}. Контакт {fio}, {dept}."]),
    "Misconduct": group(
        ["Нарушение регламента", "Инцидент информационной безопасности", "Подозрительное поведение"],
        ["Зафиксировано нарушение регламента пользователем {fio}. Подразделение {dept}, локация {loc}.",
         "Требуется проверка инцидента ИБ с участием {fio}. Хост {host}, ошибка {err}.",
         "Подозрительное поведение в системе {app}. Контакт {fio}, требуется разбирательство."]),
    "Missing Item": group(
        ["Отсутствует комплектующая", "Пропало оборудование", "Недостаёт элемента"],
        ["Отсутствует комплектующая на рабочем месте пользователя {fio}, {loc}.",
         "Не обнаружено {asset} на объекте {loc}. Контакт {fio}, подразделение {dept}.",
         "Пропало оборудование, требуется разбирательство. Контакт {fio}, ошибка {err}."]),
    "Network Folder Failure": group(
        ["Недоступна сетевая папка", "Нет доступа к общему каталогу", "Ошибка сетевого ресурса"],
        ["Недоступен сетевой каталог для пользователя {fio}, подразделение {dept}. Ошибка {err}, хост {host}.",
         "Сетевая папка не открывается у пользователя {fio}. Локация {loc}, ошибка {err}.",
         "Ошибка доступа к общему ресурсу. Контакт {fio}, требуется проверка прав."]),
    "No Dial Tone": group(
        ["Нет гудка", "Не работает телефон", "Нет сигнала линии"],
        ["IP-телефон пользователя {fio} не регистрируется, гудка нет. Локация {loc}, {dept}.",
         "Отсутствует сигнал линии на устройстве {asset}. Контакт {fio}, ошибка {err}.",
         "Телефон не работает после переустановки. Пользователь {fio}, {loc}."]),
    "Non-Delivery": group(
        ["Письма не доставляются", "Не доходит почта", "Ошибка доставки"],
        ["Письма не доставляются адресату, сервер возвращает {err}. Отправитель {fio}, {dept}.",
         "У пользователя {fio} не доставляются входящие сообщения. Хост {host}, локация {loc}.",
         "Ошибка маршрутизации почты. Контакт {fio}, ошибка {err}."]),
    "Out of Disk Space": group(
        ["Закончилось место на диске", "Диск переполнен", "Недостаточно дискового пространства"],
        ["На устройстве {host} закончилось свободное место, запись данных невозможна. Пользователь {fio}, {dept}.",
         "Диск переполнен, ошибка {err}. Локация {loc}, требуется очистка или расширение.",
         "Недостаточно места для работы {app}. Контакт {fio}, хост {host}."]),
    "Performance": group(
        ["Медленно работает система", "Высокая загрузка ресурсов", "Долгий отклик системы"],
        ["Устройство {host} отвечает медленно с {time}. Пользователь {fio}, {dept}, локация {loc}.",
         "Высокая загрузка ресурсов на {host}, работа затруднена. Контакт {fio}, {dept}.",
         "Длительное время отклика {app}. Логи содержат {err}, пользователь {fio}."]),
    "Performance Issue": group(
        ["Тормозит приложение", "Низкая производительность системы", "Долгий отклик приложения"],
        ["{app} отвечает медленно у пользователя {fio}. Хост {host}, локация {loc}, ошибка {err}.",
         "Производительность {app} снизилась с {time}. Подразделение {dept}, контакт {fio}.",
         "Долгий отклик {app} при выполнении операций. Ошибка {err}, хост {host}."]),
    "Printer Failure": group(
        ["Не работает принтер", "Ошибка печати", "Заменить картридж"],
        ["Печать на {asset} не выполняется, ошибка {err}. Пользователь {fio}, {dept}.",
         "Требуется замена картриджа в {asset}. Локация {loc}, контакт {fio}.",
         "Принтер недоступен с рабочего места {host}. Подразделение {dept}, ошибка {err}."]),
    "Service Desk": group(
        ["Обращение в службу поддержки", "Запрос помощи", "Консультация специалиста"],
        ["Пользователь {fio} ({dept}) обращается в службу поддержки. Локация {loc}, хост {host}.",
         "Требуется помощь специалиста по {app}. Контакт {fio}, ошибка {err}.",
         "Запрос на сопровождение от пользователя {fio}. Подразделение {dept}."]),
    "Software Failure": group(
        ["Сбой программного обеспечения", "Ошибка в программе", "Приложение работает некорректно"],
        ["{app} завершается с ошибкой {err} у пользователя {fio}. Хост {host}, локация {loc}.",
         "Сбой программного обеспечения на {host}. Подразделение {dept}, контакт {fio}.",
         "Приложение {app} работает некорректно, ошибка {err}. Пользователь {fio}."]),
    "Software Installation": group(
        ["Требуется установка ПО", "Настройка программного обеспечения", "Установить приложение"],
        ["Пользователь {fio} ({dept}) запрашивает установку {app} на {host}. Локация {loc}.",
         "Требуется настроить {app} для сотрудника {fio}. Подразделение {dept}.",
         "Запрос на установку программного обеспечения. Контакт {fio}, хост {host}."]),
    "Software Request": group(
        ["Запрос на программное обеспечение", "Требуется лицензия", "Выдать доступ к приложению"],
        ["Пользователь {fio} ({dept}) запрашивает доступ к {app}. Локация {loc}.",
         "Требуется лицензия на {app} для сотрудника {fio}. Хост {host}.",
         "Запрос на предоставление программного обеспечения. Контакт {fio}, {dept}."]),
    "Telephone Failure": group(
        ["Не работает телефон", "Проблемы с телефонией", "Не проходят звонки"],
        ["IP-телефон пользователя {fio} не работает. Локация {loc}, подразделение {dept}.",
         "Прерывается связь при звонках, устройство {asset}. Пользователь {fio}, ошибка {err}.",
         "Не проходят звонки с рабочего места {host}. Контакт {fio}, локация {loc}."]),
    "Virus/Trojan ": group(
        ["Обнаружен вирус", "Вредоносное ПО", "Заблокирован файл антивирусом"],
        ["Средство защиты на {host} обнаружило угрозу при открытии файла. Пользователь {fio}, ошибка {err}.",
         "Пользователь {fio} ({dept}) получил письмо с подозрительным вложением. Локация {loc}.",
         "Обнаружено вредоносное ПО на рабочем месте {host}. Требуется проверка ИБ, {dept}."]),
    "Voicemail Issue": group(
        ["Проблемы с голосовой почтой", "Не работает автоответчик", "Не приходят голосовые сообщения"],
        ["Не работает голосовая почта у пользователя {fio}. Подразделение {dept}, локация {loc}.",
         "Голосовые сообщения не доставляются. Контакт {fio}, устройство {asset}, ошибка {err}.",
         "Ошибка автоответчика на рабочем месте {host}. Пользователь {fio}, {dept}."]),
}

STATUS_FLOW = [("Logged", 0.45), ("Active", 0.25), ("Waiting for Resolution", 0.12),
               ("Resolved", 0.10), ("Closed", 0.08)]
STATUS_WEIGHT = dict(STATUS_FLOW)

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", " ": "-",
}


def translit(text):
    return "".join(TRANSLIT.get(ch.lower(), ch) for ch in text)


class RateLimiter:
    def __init__(self, rate):
        self.interval = 1.0 / rate if rate and rate > 0 else 0
        self.lock = threading.Lock()
        self.next_time = time.monotonic()

    def wait(self):
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            if now < self.next_time:
                time.sleep(self.next_time - now)
                now = time.monotonic()
            self.next_time = max(now, self.next_time) + self.interval


class IncidentFactory:
    def __init__(self, skeletons, reference, seed, owner_pool=None,
                 combos=None, variation=None):
        if not skeletons and not combos:
            raise ValueError("Пустые скелеты и комбинации")
        self.skeletons = skeletons or []
        self.ref = reference or {}
        self.seed = seed
        self.owner_pool = owner_pool or {}
        self.defaults = self.ref.get("defaults", {})
        self.email_domain = self.ref.get("email_domain", "example.com")
        self.team_usage = {}
        self.usage = collections.defaultdict(collections.Counter)
        bad_profiles = {
            "A3CC26C6687C42BF9E8501A90A02BD08",
            "5D411FA1DCA7482588E505883B6A1610",
            "4321CF5001A341F0B4254F4AB29B8831",
        }
        self.profiles = [p for p in (self.ref.get("profiles") or []) if p not in bad_profiles]
        bad_sources = {
            # Closed/Resolved + Source=Chat -> DataLayer.PromptException
            "2C6B9DDD886D4C25B7F194614FFCBBBB",
        }
        self.source_values = [s for s in (self.ref.get("enums", {}).get("Source_Valid", {}) or {})
                              if s not in bad_sources]
        self.cause_values = list(self.ref.get("enums", {}).get("CauseCode_Valid", {}).keys())
        self.variation = variation or {}
        self.vary_values = self.variation.get("values", {})
        self.status_pairs = [tuple(p) for p in self.variation.get("statuses", [])]
        self.cause_names = self.ref.get("enums", {}).get("CauseCode_Valid", {})

        # Пул комбинаций service+category+actualcategory, выровненный по сервисам:
        # сервисы чередуются по кругу, внутри сервиса по кругу чередуются категории.
        self.combos = combos or []
        bad_pairs = {("FB884D18F7B746A0992880F2DFFE749C", "430F01AC03E8428A9225FA9CB6ED7ED7")}
        by_service = collections.OrderedDict()
        for c in self.combos:
            combo = c.get("combo") or {}
            key = f"{combo.get('Service_Valid')}|{combo.get('Category_Valid')}"
            options = self.owner_pool.get(key) or []
            usable = [o for o in options if (o.get("Owner_Valid"), o.get("OwnerTeam_Valid")) not in bad_pairs]
            if options and not usable:
                continue
            by_service.setdefault(combo.get("Service_Valid"), []).append(c)
        self.service_lists = [v for v in by_service.values() if v]

    def _pick_base(self, index):
        if self.combos:
            if not self.service_lists:
                return {}
            svc_list = self.service_lists[index % len(self.service_lists)]
            return svc_list[(index // len(self.service_lists)) % len(svc_list)]
        return random.Random(self.seed + index).choice(self.skeletons)

    def _pick_status(self, rng):
        if not self.status_pairs:
            return None, None
        weights = [STATUS_WEIGHT.get(name, 0.05) for _, name in self.status_pairs]
        valid, name = rng.choices(self.status_pairs, weights=weights, k=1)[0]
        return valid, name

    def _pick_owner(self, rng, combo):
        """Выбирает допустимую пару исполнитель/команда для группы service+category,
        выравнивая частоту команд (команды должны быть разными)."""
        key = f"{combo.get('Service_Valid')}|{combo.get('Category_Valid')}"
        options = self.owner_pool.get(key)
        if not options:
            return
        # Admin + Operations отклоняется валидацией Owner на Closed/Resolved
        bad_pairs = {("FB884D18F7B746A0992880F2DFFE749C", "430F01AC03E8428A9225FA9CB6ED7ED7")}
        options = [o for o in options if (o.get("Owner_Valid"), o.get("OwnerTeam_Valid")) not in bad_pairs] or options
        min_used = min(self.team_usage.get(o["team_name"], 0) for o in options)
        candidates = [o for o in options if self.team_usage.get(o["team_name"], 0) == min_used]
        chosen = rng.choice(candidates)
        team = chosen["team_name"]
        self.team_usage[team] = self.team_usage.get(team, 0) + 1
        combo["Owner_Valid"] = chosen["Owner_Valid"]
        combo["OwnerTeam_Valid"] = chosen["OwnerTeam_Valid"]

    def build(self, index):
        rng = random.Random(self.seed + index)
        sk = self._pick_base(index)
        combo = dict(sk.get("combo", {}))
        names = dict(sk.get("names", {}))

        # Свободно варьируемые поля (проверено на API: не зависят от пары service+category)
        for field, pool in self.vary_values.items():
            if pool:
                combo[field] = rng.choice(pool)
        if self.source_values:
            combo["Source_Valid"] = rng.choice(self.source_values)
        if self.cause_values:
            combo["CauseCode_Valid"] = rng.choice(self.cause_values)
            cause_name = self.cause_names.get(combo["CauseCode_Valid"])
            if cause_name:
                combo["CauseCode"] = cause_name
        if self.profiles:
            combo["ProfileLink_RecID"] = rng.choice(self.profiles)
        status_valid, status_name = self._pick_status(rng)
        if status_valid:
            combo["Status_Valid"] = status_valid
            names["Status"] = status_name

        self._pick_owner(rng, combo)
        enums = self.ref.get("enums", {})
        for field, source in (("Service", "Service"), ("Category", "Category"),
                              ("Urgency", "Urgency_Valid"), ("Impact", "Impact_Valid"),
                              ("Priority", "Priority_Valid"), ("Source", "Source_Valid"),
                              ("CauseCode", "CauseCode_Valid")):
            if source == field:
                value = names.get(field)
            else:
                value = enums.get(source, {}).get(combo.get(source))
            if value is not None:
                self.usage[field][value] += 1
        self.usage["Status"][names.get("Status") or "?"] += 1
        category = names.get("Category") or "Service Desk"
        tmpl = TEXT_BY_CATEGORY.get(category)

        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        fio = f"{last} {first}"
        dept = rng.choice(DEPARTMENTS)
        loc = rng.choice(LOCATIONS)
        asset = rng.choice(ASSETS)
        host = rng.choice(HOSTS).format(n=rng.randint(1, 9999))
        err = rng.choice(ERROR_CODES)
        app = rng.choice(APPS)
        source = rng.choice(SOURCES)
        ctx = {"fio": fio, "dept": dept, "loc": loc, "asset": asset, "host": host,
               "err": err, "app": app, "source": source,
               "time": f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}"}

        if tmpl:
            subject = rng.choice(tmpl["subjects"])
            symptom = rng.choice(tmpl["symptoms"]).format(**ctx)
        else:
            subject = f"Обращение: {category}"
            symptom = GENERIC_REPORT.format(detail=f"инцидент категории «{category}»", **ctx)
        if rng.random() < 0.4:
            subject = f"{subject} — {host}"
        symptom += f" Обращение зарегистрировано через «{source}»."

        login = f"{translit(last)}.{translit(first)[:1]}".lower()
        email = f"{login}@{self.email_domain}"
        created = datetime.now(timezone.utc) - timedelta(
            days=rng.randint(0, 240), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        updated = created + timedelta(minutes=rng.randint(10, 10080))

        payload = dict(combo)
        payload.update({
            "Subject": subject,
            "Symptom": symptom,
            "HoursOfOperation": sk.get("HoursOfOperation") or self.defaults.get("HoursOfOperation"),
            "CreatedBy": self.defaults.get("CreatedBy", "HEATAdmin"),
            "LastModBy": self.defaults.get("LastModBy", "HEATAdmin"),
            "CreatedDateTime": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "LastModDateTime": updated.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Email": email,
            "LoginId": login,
            "IsVIP": rng.random() < 0.05,
            "FirstCallResolution": rng.random() < 0.2,
            "IsWorkAround": rng.random() < 0.15,
            "IsNotification": rng.random() < 0.1,
        })
        status_name = names.get("Status")
        if status_name in ("Resolved", "Closed"):
            payload["Resolution"] = rng.choice([
                "Проблема устранена, выполнена проверка с пользователем.",
                "Выполнена перенастройка, работоспособность восстановлена.",
                "Заменено оборудование, рабочее место проверено.",
                "Предоставлен доступ, инцидент закрыт по подтверждению пользователя.",
            ])
            payload.setdefault("TypeOfIncident", "Failure")
            if not payload.get("CauseCode"):
                payload["CauseCode"] = "Other"

        payload = {k: v for k, v in payload.items() if v is not None}
        return {"index": index, "category": category, "reporter": fio,
                "login": login, "payload": payload}


def http_request(method, url, api_key, auth_mode, auth_header, payload, timeout, insecure):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    if api_key:
        if auth_mode == "rest_api_key":
            req.add_header("Authorization", f"rest_api_key={api_key}")
        elif auth_mode == "bearer":
            req.add_header("Authorization", f"Bearer {api_key}")
        else:
            req.add_header(auth_header, api_key)
    context = None
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


FIELD_ERR_RE = re.compile(r"Field '([^']+)' was not found", re.IGNORECASE)


def push_one(meta, args, limiter, stats, stats_lock, dataset_fh, dataset_lock,
             failed_fh, failed_lock, unsupported, unsupported_lock):
    url = f"{args.base_url.rstrip('/')}/odata/businessobject/incidents"
    payload = dict(meta["payload"])
    last_error = None
    attempt = 0
    while attempt < args.retries:
        with unsupported_lock:
            bad = set(unsupported)
        for field in bad:
            payload.pop(field, None)
        meta["payload"] = payload
        attempt += 1
        limiter.wait()
        try:
            status, body = http_request("POST", url, args.api_key, args.auth_mode,
                                        args.auth_header, payload, args.timeout, args.insecure)
            if 200 <= status < 300:
                recid = ""
                try:
                    recid = (json.loads(body) or {}).get("RecId", "")
                except (ValueError, AttributeError):
                    pass
                with stats_lock:
                    stats["ok"] += 1
                with dataset_lock:
                    dataset_fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
                return True, recid
            last_error = f"HTTP {status}: {body[:400]}"
            missing = FIELD_ERR_RE.findall(body)
            if missing:
                with unsupported_lock:
                    unsupported.update(missing)
                print(f"  [schema] исключены несуществующие поля: {', '.join(sorted(set(missing)))}", flush=True)
                continue
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {body[:400]}"
            missing = FIELD_ERR_RE.findall(body)
            if missing:
                with unsupported_lock:
                    unsupported.update(missing)
                print(f"  [schema] исключены несуществующие поля: {', '.join(sorted(set(missing)))}", flush=True)
                continue
            if exc.code not in (408, 429, 500, 502, 503, 504):
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(min(2 ** attempt, 30) * (0.5 + random.random()))
    with stats_lock:
        stats["failed"] += 1
    with failed_lock:
        failed_fh.write(json.dumps({"index": meta["index"], "category": meta.get("category"),
                                    "error": last_error, "payload": payload}, ensure_ascii=False) + "\n")
    return False, last_error


def main():
    parser = argparse.ArgumentParser(description="Генерация инцидентов Ivanti Service Manager")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--base-url", default=os.environ.get("IVANTI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("IVANTI_API_KEY", ""))
    parser.add_argument("--auth-mode", choices=["rest_api_key", "bearer", "header"], default="rest_api_key")
    parser.add_argument("--auth-header", default="X-API-Key")
    parser.add_argument("--skeletons", default="skeletons.json")
    parser.add_argument("--combos", default="combos.json",
                        help="Выровненный пул service+category+actualcategory (build_combos.py)")
    parser.add_argument("--variation", default="variation.json",
                        help="Пул значений urgency/impact/priority/status")
    parser.add_argument("--owners", default="owners.json")
    parser.add_argument("--reference", default="reference.json")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--rate", type=float, default=0.0, help="Запросов в секунду (0 = без лимита)")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--dataset", default="incidents_dataset.jsonl")
    parser.add_argument("--failed", default="incidents_failed.ndjson")
    parser.add_argument("--state", default="incidents_state.json")
    parser.add_argument("--status", action="append", dest="statuses",
                        help="Ограничить статусы (можно несколько: --status Closed --status Resolved)")
    args = parser.parse_args()

    if not os.path.exists(args.skeletons):
        parser.error(f"Не найден {args.skeletons}. Сначала выполните build_skeletons.py")
    with open(args.skeletons, "r", encoding="utf-8") as fh:
        skeletons = json.load(fh)
    if not isinstance(skeletons, list) or not skeletons:
        parser.error("skeletons.json пуст или повреждён")

    combos, variation = [], {}
    if os.path.exists(args.combos):
        with open(args.combos, "r", encoding="utf-8") as fh:
            combos = json.load(fh)
    else:
        print(f"[warn] {args.combos} не найден — база берётся из скелетов (сервисы перекошены)")
    if os.path.exists(args.variation):
        with open(args.variation, "r", encoding="utf-8") as fh:
            variation = json.load(fh)

    reference = {}
    if os.path.exists(args.reference):
        with open(args.reference, "r", encoding="utf-8") as fh:
            reference = json.load(fh)

    owner_pool = {}
    if os.path.exists(args.owners):
        with open(args.owners, "r", encoding="utf-8") as fh:
            owner_pool = json.load(fh)
    else:
        print(f"[warn] {args.owners} не найден — исполнитель/команда берутся из скелета")

    factory = IncidentFactory(skeletons, reference, args.seed, owner_pool,
                              combos=combos, variation=variation)
    if args.statuses:
        wanted = {s.strip().lower() for s in args.statuses}
        factory.status_pairs = [p for p in factory.status_pairs if p[1].lower() in wanted]
        if not factory.status_pairs:
            parser.error("Нет совпадений --status с variation.json")
    mode = "PUSH" if args.push else "DRY-RUN"
    status_names = ", ".join(name for _, name in factory.status_pairs) or "из скелетов"
    print(f"Режим: {mode}; записей: {args.count}; "
          f"комбинаций: {len(combos) or len(skeletons)} (сервисов: {len(factory.service_lists)}); "
          f"групп исполнителей: {len(owner_pool)}; "
          f"статусы: {status_names}; "
          f"concurrency={args.concurrency}; rate={args.rate or 'unlimited'}")

    if not args.push:
        with open(args.dataset, "w", encoding="utf-8") as fh:
            for i in range(args.count):
                fh.write(json.dumps(factory.build(i), ensure_ascii=False) + "\n")
        print(f"Датасет сохранён: {args.dataset} ({args.count} записей)")
        for label, key in (("Сервисы", "Service"), ("Категории", "Category"),
                           ("Срочность", "Urgency"), ("Влияние", "Impact"),
                           ("Приоритет", "Priority"), ("Источник", "Source"),
                           ("Причина", "CauseCode"), ("Статус", "Status")):
            print(f"{label} ({len(factory.usage[key])}):")
            for value, count in factory.usage[key].most_common():
                print(f"  {count:5d}  {value}")
        print("Распределение команд:")
        for team, count in sorted(factory.team_usage.items(), key=lambda x: -x[1]):
            print(f"  {count:5d}  {team}")
        return

    if not args.base_url or not args.api_key:
        parser.error("Для --push нужны --base-url и --api-key (или IVANTI_BASE_URL / IVANTI_API_KEY)")

    done = set()
    if os.path.exists(args.state):
        with open(args.state, "r", encoding="utf-8") as fh:
            done = set(json.load(fh).get("done", []))

    limiter = RateLimiter(args.rate)
    stats = {"ok": 0, "failed": 0}
    stats_lock, dataset_lock, failed_lock = threading.Lock(), threading.Lock(), threading.Lock()
    unsupported, unsupported_lock = set(), threading.Lock()
    start = time.time()

    with open(args.dataset, "a", encoding="utf-8") as dataset_fh, \
            open(args.failed, "a", encoding="utf-8") as failed_fh:
        pending = [i for i in range(args.count) if i not in done]
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(push_one, factory.build(i), args, limiter, stats, stats_lock,
                            dataset_fh, dataset_lock, failed_fh, failed_lock,
                            unsupported, unsupported_lock): i
                for i in pending
            }
            processed = 0
            for future in as_completed(futures):
                processed += 1
                index = futures[future]
                try:
                    succeeded, _ = future.result()
                except Exception:
                    succeeded = False
                if succeeded:
                    done.add(index)
                if processed % 200 == 0 or processed == len(pending):
                    with stats_lock:
                        ok, failed = stats["ok"], stats["failed"]
                    print(f"  обработано {processed}/{len(pending)} | ok={ok} failed={failed} | {time.time()-start:.0f}s", flush=True)
                    with open(args.state, "w", encoding="utf-8") as sf:
                        json.dump({"done": sorted(done)}, sf)

    with stats_lock:
        ok, failed = stats["ok"], stats["failed"]
    print(f"Готово: успешно {ok}, ошибок {failed}, время {time.time()-start:.0f}s")
    if failed:
        print(f"Ошибочные записи: {args.failed}")


if __name__ == "__main__":
    main()
