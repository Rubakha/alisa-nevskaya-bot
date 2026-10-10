# API v1 для Mini App Алисы — инвентарь функций бота и что вошло

Контракт штаба: `D:\Рубаха\Рубаха проект\strategy\MINIAPP_API.md`. Код: `miniapp_api.py`, тесты: `tests/test_miniapp_api.py`.
Включение: `MINIAPP_API=1` (без флага маршрутов нет). Остальные переменные — внизу.

## 1. Инвентарь пользовательских функций бота (10.10.2026)

Меню клиента (`kb_client`): «🎀 Письмо с открыткой», «💌 Заказать письмо», «👤 Мой кабинет», «📖 Примеры», «✨ Бесплатно»,
«📖 Дневник Алисы», «❓ Помощь», «👥 Пригласить друга». Админские кнопки (заказы, рассылка, экспорт, помощник, бэкап,
партнёры, промокоды) и команды `/test_add`, `/refund`, `/promo_*`, `/card*`, `/sources`, `/comments` в API не выводятся.

| # | Функция бота (меню / callback) | Что делает | В API v1 | Ручка / причина |
|---|---|---|---|---|
| 1 | 🎀 Письмо с открыткой (`occ:catalog`, `occ:go:<повод>`) | каталог поводов, анкета 4–6 вопросов, тон, подпись | **да** | `GET /catalog`, `POST /orders` |
| 2 | Письмо ИИ + превью (`occ_finish`, masked preview) | письмо пишет ИИ, бесплатно — первая половина | **да** | `POST /orders/<id>/preview` (хвост письма наружу не отдаётся) |
| 3 | Изменить текст (`occ:retext`) | 2 бесплатных варианта, затем одно пожелание ≤500 знаков | **да** | тот же `POST …/preview` (`wish`), `retextsLeft`, `wishAvailable` |
| 4 | Другая картинка (`occ:pc:next`) | следующая готовая картинка повода из базы | **да** | `GET /cards`, `PUT /orders/<id>/card {ref}` |
| 5 | Конструктор открытки (`cc:*`: палитра, реквизит ≤3, раскладка, рамка, анимация) | в боте скрыт по решению владельца 08–10.10, код жив | **да** | `GET /card-options`, `PUT …/card {spec}`; реквизит только по выбору клиента |
| 6 | Превью открытки с водяным знаком | рендер `postcards.render(preview=True)` | **да** | `GET /orders/<id>/card-preview.jpg?ref=` (заголовок или подписанная ссылка) |
| 7 | Промокод (`pr:enter`), цена со скидкой | промокоды, партнёрские коды, скидка друга, баланс, одна наибольшая скидка | **да** | `POST /promo/check`, `promo` в `POST …/pay` |
| 8 | Оплата ЮKassa (`occ:buy`, `yk:check`) | заказ, ссылка оплаты, «Я оплатил(а)», опрос платежей | **да** | `POST …/pay`, `POST …/pay/check`; заказ тот же `orders/*.json` |
| 9 | Оплата 0 ₽ при полной скидке | выдача без платежа | **да** | `paid:true, price:0` в ответе `pay` |
| 10 | Тестовая оплата (`TEST_USERS`, `test:pay`) | 0 ₽ для тестовых аккаунтов | **да** | `POST …/pay` у тестового аккаунта: заказ 0 ₽, `is_test`, ЮKassa не трогаем, в статистику не идёт; ответ `{paid:true, price:0, realPrice, test:true}` |
| 11 | Оплата встроенным счётом Telegram (`send_invoice`) | если ЮKassa-магазин не подключён | **нет** | нужен чат с ботом; API отвечает `503 payment_unavailable` + `botUrl` |
| 12 | Выдача: открытка + письмо + оценка (`occ_deliver`) | уходит в чат бота и доступно в API | **да** | `GET /orders/<id>` (`letter`, `envelopeUrl`), `GET …/card.jpg` |
| 13 | Отправка получателю (`occ_send_options`, inline, `miniapp_share`) | ссылка-конверт `/e/<код>`, shareMessage | **да** | `POST …/send` → `envelopeUrl`, `preparedMessageId`, `shareUrl` |
| 14 | Оценка письма (`rate:`) | 1–5 звёзд, админу уведомление | **да** | `POST …/rate` |
| 15 | AI-картинка (`ai:*`, `ai_card`) | после оплаты, кнопочные параметры, ≤3 попыток, потолок в сутки | **да** | `POST …/ai-card`, `GET …/ai-card/<jobId>`, `PUT …/card {ref:"ai:…"}`; выключено без `WAVESPEED_API_KEY` |
| 16 | 👤 Мой кабинет (`cab:home`, `cab:read`, `cab:send`) | заказы, потрачено, бонусы, повторная отправка | **да** | `GET /me`, `GET /me/orders`, `POST …/send` |
| 17 | Оплатить/отменить неоплаченное (`cab:pay`, `cab:cancel`) | | **да** | `POST …/pay`, `POST …/cancel` |
| 18 | 👥 Пригласить друга / «Подари подруге −10%» (`ref:gift`) | реферальная ссылка, −10% другу, 50 ₽ на баланс | **да** | `POST /me/invite`, `GET /me` (`referralLink`, `bonusRub`) |
| 19 | 🔔 Напоминания о датах и подписки на праздники (`cal:*`) | даты близких, напоминание за 3 дня, праздники, рассылка | **да (v1 + v2)** | `GET/POST /me/reminders`, `DELETE /me/reminders/<id>`; **v2:** `GET/PUT /me/subscriptions` (праздники и `mailing` — отписка от рассылок владельца) |
| 20 | 📖 Примеры | демо-письмо и три открытки | **да** | `GET /examples`, `GET /examples/<n>.jpg` |
| 21 | 📖 Дневник Алисы | записи канала | **да** | `GET /diary` (кэш 10 мин, с t.me/s) |
| 22 | ✨ Бесплатно (`free:letter`, `free:pdf`) | начало письма (диагностика), PDF «50 фраз» | **частично** | `GET /free` — список со ссылками в бота; сами сценарии и отправка PDF остаются в боте |
| 23 | Голосовое письмо (`vo:*`, `voice_flow`, `voice_letter`) | озвучка ElevenLabs +100 ₽, подложка, мужской/женский голос | **да (v2)** | `GET /voice/options`, `PUT /orders/<id>/voice` (до оплаты, в сумму), `POST …/voice/pay` и `/voice/check` (доплата после), `GET …/voice` + `voice.mp3/ogg`; лимиты символов `VOICE_DAILY_CAP`, `VOICE_MONTHLY_CAP`, тестеры бесплатно |
| 24 | Письмо группой (`grp:*`, `group_letters`) | организатор + до 12 участников, 399 ₽ | **да (v2)** | `GET /groups/options`, `POST/GET /groups`, `GET/DELETE /groups/<code>`, `POST …/answers`, `…/build`, `…/remind`; приглашение `t.me/<бот>/app?startapp=grp_<код>`; дальше обычный заказ |
| 25 | Набор «3 письма» (`occ:pack`, `occ:credit`) | предоплаченные письма, 449 ₽ | **да (v2)** | `GET /pack`, `POST /pack/pay`, `POST /purchases/<id>/check`, оплата письма кредитом `POST /orders/<id>/pay {useCredit:true}` |
| 26 | PDF «Разговор с папой» (`occ:doc`) | платный документ 149 ₽ | **да (v2)** | `GET /doc`, `POST /doc/pay`, `GET /doc/<id>`, файл `…/file.pdf` по подписанной ссылке (и в чат бота) |
| 27 | Конверт получателя (`env:open`, `/start g_<код>`) | вскрытие письма | **нет** (вне API) | работает на сайте `/e/<код>` и в боте |
| 28 | Партнёрская программа (`pt:*`, `pa:*`, `/partner`) | кабинет партнёра, ссылки, выплаты | **нет** | отдельный контур, админ/партнёр в боте |
| 29 | Заказ из VK, `vk_bridge`, `vk_shop` | другой канал | **нет** | вне Mini App |
| 30 | Старый диагностический заказ «письмо без открытки» (`order:*`, `giftpain`) | | **нет** | замещён «письмами с открыткой» |
| 31 | ❓ Помощь, `sup:new` | обращения в поддержку | **да (v2)** | `POST /support` (≤3 в час, ≤10 в сутки), `GET /me/support`; ответ владельца (`/reply`) приходит в бота и отмечается в списке |
| 32 | Метки источников `start=ig_/vk_/ad_/p_…` | атрибуция, партнёр на 30 дней | **отложено** | нужен `startParam` Mini App → `POST /me/attribution`; пока партнёрские коды работают через промокод |

## 2. Что в API v1

База `/api/v1`, JSON UTF-8. Ошибки: `{"error":{"code","message"}}` (+ `retryAfter` при 429).
Авторизация: заголовок `X-Telegram-Init-Data` (подпись на токене бота, срок 24 ч). Публичные: каталог, карточки, примеры, дневник.
Для `<img>`: ссылки `previewUrl`/`url` в ответах подписаны (час жизни), заголовок не нужен.

Публичные: `GET /catalog`, `GET /cards?occasion=&season=&limit=&cursor=`, `GET /cards/<ref>.jpg?size=thumb|full`,
`GET /card-options`, `GET /examples`, `GET /examples/<n>.jpg`, `GET /diary`, `GET /free`.

Заказ: `POST /orders`, `POST /orders/<id>/preview`, `GET /orders/<id>`, `PUT /orders/<id>/card`,
`GET /orders/<id>/card-preview.jpg`, `GET /orders/<id>/card.jpg`, `POST /promo/check`, `POST /orders/<id>/pay`,
`POST /orders/<id>/pay/check`, `POST /orders/<id>/cancel`, `POST /orders/<id>/ai-card`, `GET /orders/<id>/ai-card/<jobId>`,
`GET /orders/<id>/ai-card/<n>.jpg`, `POST /orders/<id>/send`, `POST /orders/<id>/rate`.

Кабинет: `GET /me`, `GET /me/orders`, `DELETE /me/orders/<id>`, `POST /me/orders/clear-opened`, `GET/POST /me/reminders`, `DELETE /me/reminders/<id>`, `POST /me/invite`, `POST /me/attribution`.

Статусы заказа: `draft` → `preview` → `awaiting_payment` → `delivered` (выдан после оплаты; отдельного `paid` нет, выдача мгновенная).

Заказ из Mini App — те же файлы `orders/*.json`, что у бота: после оплаты выдача идёт и в чат бота (как всегда), а в API
доступна через `GET /orders/<id>`. Черновик (до оплаты) хранится в `DATA_DIR/miniapp_drafts/<id>.json` и после «Оплатить»
превращается в обычный заказ с тем же номером (`occ_make_order`).

## 2.1. Форматы ответов (правки 10.10 вечером)

Ошибки везде одинаковы: `{"error":{"code","message"}}`, при 429 ещё `retryAfter`.

**Тестовая оплата** — `POST /orders/<id>/pay` у аккаунта из `TEST_USERS`:
`200 {"paid":true,"price":0,"realPrice":199,"test":true}`. Заказ создаётся как в боте (`occ_make_order`), помечается `is_test`,
`price_rub=0` (настоящая цена в `test_price_rub`), выдаётся через `fulfill_order` (`charge_id=test-…`), ЮKassa не вызывается,
в статистику, финансы и рассылки заказ не идёт. Повторный вызов: `{"paid":true,"price":0,"test":true}`. Дальше `send`, `rate`, кабинет работают как у обычного заказа.
В `GET /orders/<id>` и `GET /me/orders` у такого заказа `price` = настоящая цена, `test:true` (на экране показывать цену и пометку «тест»).

**Заказы, созданные в самом боте** (черновика Mini App нет). Все ручки `/orders/<id>…` понимают и их, если заказ
выдан (`done`) или ждёт оплаты (`pending`) и принадлежит клиенту; повод должен быть из каталога писем с открыткой.
- `GET /orders/<id>` → то же, что для заказа из приложения: `{orderId, product, status:"delivered"|"awaiting_payment", giftFor, sign, price, cardRef, cardTitle, cardLine, previewUrl, letter (только после оплаты), locked:false, envelopeCode, envelopeUrl, opened, rating, aiCard, test}`.
- `GET /orders/<id>/card.jpg` — чистая открытка (оплаченный заказ): по `X-Telegram-Init-Data` или по подписанной ссылке `previewUrl` (`?exp=&sig=`, час жизни). Чужому — 404, до оплаты — 404.
- `GET /orders/<id>/card-preview.jpg?ref=` — превью с водяным знаком для заказа, ждущего оплаты.
- Отдельный файл черновика для таких заказов не создаётся; «изменить текст» для них недоступно (`409 locked`), оплата и отмена работают.

**Кабинет**
- `GET /me/orders` → `{"items":[{orderId, product, icon, title, giftFor, status:"delivered"|"awaiting_payment"|"preview", price, test, opened, createdAt, cardRef, envelopeUrl?, previewUrl?}]}`.
  `opened` — открыл ли получатель (есть всегда); скрытые клиентом заказы (`hidden_by_client`) и отменённые не выдаются.
- `DELETE /me/orders/<id>` → `{"ok":true,"action":"cancelled"|"hidden"|"deleted"}`:
  неоплаченный заказ — отмена (`cancelled`, как `cancel`); выданный — только скрыть у клиента (`hidden`: запись, деньги и учёт не трогаем,
  ссылка-конверт получателя работает, по прямой ссылке заказ читается); черновик без заказа — удаляется (`deleted`). Чужой заказ — `404`.
- `POST /me/orders/clear-opened` → `{"ok":true,"hidden":N}` — скрывает все выданные письма, которые получатель уже открыл (`opened_at`). Повтор: `hidden:0`.
- В кабинете бота скрытые письма тоже не показываются в списке (в счётчиках остаются).

## 2.2. Короткое превью письма и тестеры по нику (10.10)

**Превью письма** (`POST /orders/<id>/preview`, `GET /orders/<id>` до оплаты). Сервер отдаёт только приветствие и первую
короткую фразу-зацепку: не больше 220 знаков и не больше 25% текста, обрыв по слову, в конце «…». Остального текста, включая концовку,
подпись и имена из конца письма, в ответе нет вообще. Формат:
`{"letterPreview":"Катя, привет. Первый абзац…","locked":true,"previewHidden":true,"lockedChars":780,"hiddenParagraphs":2,"retextsLeft":2,"wishAvailable":false,"cardRef":"birthday-001","previewUrl":"…"}`.
Для надписи «Дальше ещё N абзацев» — `hiddenParagraphs` (абзацы, до которых превью не дошло; частично показанный абзац считается показанным),
`lockedChars` — число скрытых знаков. Полный текст (`letter`) приходит только после оплаты.

**Тестеры по нику.** Переменная `TEST_USERNAMES` (по умолчанию `Victoria_Christmas,SerRubakha`, без `@`, регистр не важен). При первом
обращении с подписанным initData (любая ручка с авторизацией) или `/start`, где `username` совпал, бот запоминает `chat_id`
в `test_users.json` (так же, как `/test_add`) и дальше работает по `chat_id`: смена ника ничего не ломает. Ник берётся из подписанных
Telegram данных — подделать нельзя. Админ управляет как раньше: `/test_add @ник`, `/test_off @ник` (отключённого админом по нику заново не включаем),
`/test_list`. Для тестера оплата 0 ₽, заказ `is_test`, вне статистики (см. 2.1).
- `GET /me` → добавлено `isTester: true|false` (интерфейс показывает «Тестовый режим: письма бесплатные»).
- Суточные потолки тестера отдельные и на человека: `MINIAPP_TESTER_GEN_CAP` (писем, по умолчанию 30) и `MINIAPP_TESTER_AI_CAP`
  (AI-картинок, по умолчанию 10); общие потолки тестер не расходует. Часовой лимит писем (6) и «≤3 попытки на заказ» остаются.
  Ответ при исчерпании: `429 {"error":{"code":"daily_cap","message":"Тестовый лимит … на сегодня исчерпан…"},"retryAfter":3600}`.

## 3. Защита денег и ограничений (как в боте)
- Хвост письма до оплаты не отдаётся ни в одной ручке; чистая открытка без «превью» — только после оплаты.
- Цена считается на сервере (`partners.quote`: одна наибольшая скидка, баланс); клиент цену не присылает.
- Тексты: 2 бесплатных варианта + 1 пожелание; не более 6 генераций письма в час на человека (`GEN_PER_HOUR`) и
  `MINIAPP_GEN_DAILY_CAP` (300) в сутки на всех. Неоплаченные: 1 штука (как `MAX_PENDING` в боте), ≤10 черновиков в сутки.
- AI-картинка: только после оплаты, только кнопочные параметры (стиль, сюжет, палитра, ≤2 детали), ≤3 попытки на заказ,
  суточный потолок `AI_CARD_DAILY_CAP`, попытка не тратится при сбое.
- 30 запросов в минуту на человека, 120 в минуту на IP для публичных ручек.
- CORS: `https://alisa-letters.pages.dev` и `MINIAPP_ORIGINS`.
- В ответах и логах нет токенов, ключей и initData; ссылки на картинки подписаны HMAC.

## 4. Запуск локально
```
set MINIAPP_API=1
set TG_BOT_TOKEN=<тестовый токен> & set ADMIN_ID=1 & set DATA_DIR=%TEMP%\alisa_data
python bot_best.py          # polling + Flask на порту STATS_PORT (по умолчанию 80)
```
Для фронтенда в dev-режиме добавьте адрес в `MINIAPP_ORIGINS` (например `http://localhost:3000`) и подпишите initData тестовым токеном
(см. `tests/test_miniapp_api.py`, функция `init_data`). Тесты: `python tests/test_miniapp_api.py` — без сети и платных API.

## 5. Переменные окружения
`MINIAPP_API=1` — включить. `MINIAPP_ORIGINS` — дополнительные адреса фронтенда через запятую.
`ENVELOPE_BASE` — адрес для ссылок-конвертов получателя (`{ENVELOPE_BASE}/e/<код>`, по умолчанию адрес сайта бота; сайт-приложение проксирует `/e/<код>` на бота, og-метки превью строятся на том же домене). `MINIAPP_SHORTNAME` — короткое имя Main Mini App (по умолчанию `app`): реферальная ссылка `https://t.me/<бот>/app?startapp=ref_<chat_id>` и приглашение группы `…?startapp=grp_<код>`. Старые ссылки `?start=ref_<id>` работают как раньше. `MINIAPP_RETURN_URL` — куда вернуть после оплаты (по умолчанию `https://alisa-letters.pages.dev`, добавляется `/?paid=<id>`).
`MINIAPP_GEN_DAILY_CAP` — писем ИИ в сутки (300). `TEST_USERNAMES`, `MINIAPP_TESTER_GEN_CAP`, `MINIAPP_TESTER_AI_CAP` — тестеры (см. 2.2). `WAVESPEED_API_KEY`, `AI_CARD_DAILY_CAP` — как у бота. Для оплаты — `YOOKASSA_SHOP_ID/SECRET_KEY`
(и `YOOKASSA_RECEIPT=1` → в `pay` нужна почта `email`).

## 6. Чего не хватает дальше
Реализовано в v2 (раздел 7): голос, письмо группой, набор «3 письма», PDF, поддержка, подписки. Осталось: атрибуция по `startParam` уже есть (`POST /me/attribution`);
веб-хук оплаты вместо опроса (сейчас бот опрашивает ЮKassa каждые 15 с и фронтенд вызывает `…/check`); оплата Telegram-счётом из приложения;
каталожная «Голосовое письмо за 390 ₽» не вынесена отдельной покупкой — тот же результат даёт переключатель +100 ₽ (`PUT …/voice`);
общий лимит запросов хранится в памяти процесса (одна реплика на Amvera — достаточно).

## 7. v2: контракт (реализован в коде, тесты `tests/test_miniapp_v2.py`)

Закрывает строки инвентаря №19 (подписки), №23 (голос), №24 (письмо группой), №25 (набор «3 письма»), №26 (PDF), №31 (поддержка).
Все ручки — тот же `/api/v1`, тот же `X-Telegram-Init-Data`, тот же формат ошибок `{"error":{"code","message"}}` (+`retryAfter` при 429),
тот же лимит 30 запросов в минуту на человека. Включаются флагом `MINIAPP_API_V2` (по умолчанию `1`, если включён `MINIAPP_API`;
`MINIAPP_API_V2=0` — новых ручек нет, v1 работает как раньше). Цены на сервере (клиент их не присылает); тестерам (`isTester`) всё 0 ₽ с `test:true`,
как в 2.1/2.2. Картинки, аудио и файлы отдаются по подписанным ссылкам (`?exp=&sig=`, час жизни), заголовок для них не нужен.
Все платежи — те же: `paymentUrl` ЮKassa → возврат `…/?paid=<id>` → опрос `check`. Текст ошибок на русском, секретов в ответах нет.

### 7.1. Голосовое письмо (+100 ₽, ElevenLabs)

Два пути, как в боте: **до оплаты** (переключатель на превью — озвучка входит в сумму) и **после оплаты** (доплата за уже выданное письмо).
Голос Алисы (модель, подача, подложка −22 dB, сезонный зимний трек) не меняется — настройки те же, что в боте.

- `GET /voice/options` (публичная) → `{"enabled":true,"price":100,"genders":[{"key":"f","label":"Женский голос"},{"key":"m","label":"Мужской голос"}],"music":true,"maxLetterChars":9000}`.
  `enabled:false`, если нет ключа ElevenLabs или исчерпан лимит месяца/суток — тогда экран озвучки скрыть.
- `PUT /orders/<id>/voice` `{"enabled":true,"gender":"f"}` — **до оплаты** (черновик с письмом, без начатой оплаты). Включает озвучку:
  в `price` заказа (в ответе `GET /orders/<id>`) добавляется +100 ₽ (скидки считаются как у обычного письма), `voice:{enabled,gender,price}`.
  `{"enabled":false}` — убрать. Ответ: `{"ok":true,"price":299,"voice":{"enabled":true,"gender":"f","price":100}}`.
  Нельзя вместе с кредитом набора и с письмом группой (как в боте). После оплаты озвучка стартует сама.
- `POST /orders/<id>/voice/pay` `{"gender":"f","email"?}` — **после оплаты** письма: создаёт доплату. Ответ как у `pay`: `{"paymentUrl":"…","price":100,"voiceOrderId":"ALI-…"}`;
  тестер: `{"paid":true,"price":0,"realPrice":100,"test":true,"status":"queued"}`. Лимиты проверяются ДО создания платежа.
- `POST /orders/<id>/voice/check` → `{"paid":true|false,"status":"awaiting_payment|queued|working|ready|failed"}` — опрос оплаты доплаты (то же, что «Я оплатил(а)»).
- `GET /orders/<id>/voice` → состояние озвучки (опрос каждые 3–5 с, готово обычно за 30–90 с):
  `{"available":true,"status":"none|awaiting_payment|queued|working|ready|failed","gender":"f","price":100,"chars":1840,"audioUrl":"…/voice.mp3?exp&sig","oggUrl":"…/voice.ogg?exp&sig","error":null,"updatedAt":"…"}`.
  `audioUrl`/`oggUrl` только при `ready`; `failed` — деньги не возвращаем автоматически, владелец получает уведомление и повторяет вручную (`/voice_retry`);
  человеку показать «Голос пока не записался, письмо и открытка на месте — пришлём вручную».
  Готовое голосовое дублируется в чат бота (как сейчас) и привязывается к письму: получатель на странице конверта слышит его.
- `GET /orders/<id>/voice.mp3`, `GET /orders/<id>/voice.ogg` — файлы (заголовок или подписанная ссылка), `404`, пока не `ready`.
- Ошибки: `403 not_paid` (доплата до оплаты письма), `409 already_voiced`, `409 voice_in_progress`, `409 letter_too_long` (письмо длиннее 9000 знаков),
  `503 voice_unavailable` (нет ключа/ffmpeg), `429 voice_cap` (лимит). Лимиты: суточный потолок символов `VOICE_DAILY_CAP` (по умолчанию 8000) и месячный
  `VOICE_MONTHLY_CAP` (по умолчанию 15000) считаются по журналу `voice_usage.jsonl` вместе с символами заказа; тестерам — свой суточный `MINIAPP_TESTER_VOICE_CAP` (3000).
  Если озвучка не поместится в остаток — отказ до оплаты, деньги не списываются.

### 7.2. Письмо группой («от всех нас», 399 ₽)

Та же логика, что в `group_letters.py`: организатор создаёт группу, зовёт до 12 участников, каждый отвечает на 2 вопроса и подписывается,
организатор (минимум 2 части) собирает общее письмо и оплачивает как обычный заказ. Тексты участников видны только ИИ при сборке — друг другу и организатору не показываются (только подписи).

- `GET /groups/options` (публичная) → `{"price":399,"maxParticipants":12,"minToBuild":2,"occasions":[{"key":"family","icon":"🌷","title":"…"}],"questions":[{"key":"memory","text":"Одно воспоминание о {name}…"},{"key":"thanks","text":"…"}]}`
  (повод — из `GROUP_OCCASIONS`; в `text` подставлено `{name}` — имя получателя берётся из группы, см. `GET /groups/<code>`).
- `POST /groups` `{"occasion":"family","name":"Мама","who":"наша мама","sign":"Твои дети"}` (каждое поле ≤60 знаков) →
  `{"code":"aB3dE","status":"open","inviteUrl":"https://t.me/<бот>/app?startapp=grp_aB3dE","botUrl":"https://t.me/<бот>?start=grp_aB3dE","shareText":"💌 Собираем общее письмо для Мамы…","shareUrl":"https://t.me/share/url?…"}`.
  Лимит: ≤5 открытых групп на человека. `inviteUrl` — ссылка Main Mini App: интерфейс получает `start_param=grp_<код>` и открывает экран участника.
- `GET /groups` → мои группы `{"items":[{code,name,who,occasion,status:"open|built|ordered",participants:3,createdAt,orderId?}]}`.
- `GET /groups/<code>` — для любого авторизованного, но состав ответа зависит от роли:
  участнику (по ссылке) `{"code","name","who","organizerName","status","isOrganizer":false,"alreadyAnswered":false,"full":false,"questions":[…с подставленным именем…]}`;
  организатору дополнительно `{"participants":[{"sign":"Аня","isMe":true,"at":"…"}],"count":3,"canBuild":true,"shareText","shareUrl","inviteUrl","orderId":null,"price":399}`.
  Закрытая группа (`status != open`) участнику: `200` с `status` и без вопросов (экран «письмо уже собрано»); несуществующая — `404`.
- `POST /groups/<code>/answers` `{"memory":"…","thanks":"…","sign":"Аня"}` (memory/thanks 10–800 знаков, sign ≤40) → `{"ok":true,"count":3}`;
  повторная отправка заменяет прежний ответ этого человека. Организатор отвечает так же (его часть тоже входит). Ошибки: `409 group_closed`, `409 group_full`, `400 bad_answers`.
  Организатору приходит сообщение в бота «✍️ <подпись> написал(а) свою часть. Уже: N.» (как сейчас).
- `POST /groups/<code>/build` (только организатор, ≥2 части, группа `open`) — синхронно (до ~60 с) собирает письмо ИИ, лимиты генерации как у `preview`,
  создаёт обычный черновик заказа повода группы с ценой 399 → `{"orderId":"ALI-…","status":"preview", …тот же ответ, что у POST /orders/<id>/preview (короткое letterPreview, hiddenParagraphs, cardRef, previewUrl)}`.
  Дальше всё как для обычного заказа: `PUT …/card`, `POST …/preview` (перегенерация: 2 бесплатных + 1 пожелание, склеивает тех же участников), `POST …/pay` (399 ₽, промокод разрешён), `send`, `rate`.
  Повторный `build` по той же группе возвращает тот же `orderId` (`409 already_built`, если заказ уже оплачен). `412 not_enough` — меньше двух частей.
  Нельзя оплатить кредитом набора и добавить озвучку (как в боте).
- `POST /groups/<code>/remind` → `{"text":"🤍 Напоминаю про общее письмо…","shareUrl":"…"}` — готовый текст-напоминание для отправки тем, кто не ответил.
- `DELETE /groups/<code>` (организатор, пока не оплачено) → `{"ok":true}` — закрыть и скрыть группу.
- Метрики: группы тестеров не учитываются (`is_test`).

### 7.3. Набор «3 письма» (449 ₽) и кредиты

- `GET /pack` (публичная; с initData добавляет `myCredits`) → `{"key":"pack3","title":"Набор: 3 письма с открыткой","price":449,"credits":3,"regularPrice":597,"savings":148,"myCredits":0}`.
- `POST /pack/pay` `{"promo"?, "email"?}` → `{"orderId":"ALI-…","paymentUrl":"…","price":449}` (скидки как у обычного заказа; тестер: `{"paid":true,"price":0,"test":true,"credits":3}`).
  Неоплаченный заказ ограничен одним (как `MAX_PENDING`): `409 pending_exists` + `orderId`.
- `POST /purchases/<orderId>/check` → `{"paid":true,"kind":"pack","credits":3}` — общий опрос оплаты для набора и PDF; пока не оплачено `{"paid":false}`.
- Списание: `POST /orders/<id>/pay` `{"useCredit":true}` — вместо платежа списывает 1 кредит: заказ выдаётся сразу →
  `{"paid":true,"price":0,"credit":true,"creditsLeft":2}`. Ошибки: `409 no_credits`, `409 credit_not_allowed` (письмо группой / с озвучкой), `409 pending_exists`.
  В `GET /orders/<id>` и `GET /me` есть `credits`/`creditsAvailable`; интерфейс показывает кнопку «Забрать по набору (осталось N)» рядом с «Оплатить».
- `GET /me/purchases` → `{"items":[{orderId,kind:"pack|doc",title,price,status:"awaiting_payment|delivered",createdAt}]}`.

### 7.4. PDF «Разговор с папой» (149 ₽)

- `GET /doc` (публичная) → `{"key":"papa_pdf","title":"Разговор с папой (PDF)","price":149,"pages":10,"description":"30 фраз для пяти ситуаций…"}`.
- `POST /doc/pay` `{"promo"?, "email"?}` → `{"orderId","paymentUrl","price":149}`; тестер: `{"paid":true,"price":0,"test":true,"orderId"}`.
- Опрос — `POST /purchases/<orderId>/check` (см. выше) → `{"paid":true,"kind":"doc","downloadUrl":"…/doc/<orderId>/file.pdf?exp&sig"}`.
- `GET /doc/<orderId>` → `{"status":"awaiting_payment|delivered","downloadUrl"?}`; `GET /doc/<orderId>/file.pdf` — файл (`application/pdf`, заголовок или подпись), до оплаты `404`.
  После оплаты файл также уходит в чат бота (как сейчас). Ссылка на скачивание живёт час, новую выдаёт `GET /doc/<orderId>`.

### 7.5. Поддержка

- `POST /support` `{"topic":"payment|letter|app|other","text":"…"}` (text 5–1000 знаков) → `{"ticketId":"T-…","status":"sent"}`.
  Сообщение уходит админу в том же формате, что `sup:new` (имя, @ник, chat_id, текст, «Ответить: /reply …»), плюс тема, номер заказа не нужен.
  Лимит: 3 обращения в час и 10 в сутки на человека (`429 rate_limited`). Ответ владельца (`/reply`) приходит клиенту в чат бота и отмечается в `GET /me/support`.
- `GET /me/support` → `{"items":[{"ticketId","topic","text","status":"sent|answered","createdAt","reply":null,"repliedAt":null}]}` — последние 20.
  `reply` — текст ответа (после `/reply`). Интерфейс показывает «Ответ придёт сюда и в чат с ботом».

### 7.6. Подписки и рассылка

- `GET /me/subscriptions` → `{"holidays":[{"key":"mother","icon":"🤍","title":"День матери","next":"2026-11-29","subscribed":false}],"mailing":true,"reminderDates":2}`
  (праздники — из `calendar_reminders.HOLIDAYS`; `reminderDates` — сколько личных дат сохранено, управляются через `/me/reminders`).
- `PUT /me/subscriptions` `{"holidays":["mother","newyear"],"mailing":false}` — любое из полей необязательно; `holidays` — полный список подписок (неизвестные ключи игнорируются),
  `mailing:false` — не получать общие рассылки владельца (в рассылке такие клиенты пропускаются, напоминания о датах и письма по заказам приходят как обычно).
  Ответ — то же, что `GET`.

### 7.7. Прочее

- `GET /me` дополнен: `credits`, `mailing`, `voiceAvailable`, `isTester`; `GET /orders/<id>` — `voice` (состояние озвучки или `null`), `creditsAvailable`.
- Статусы заказа не меняются (`draft → preview → awaiting_payment → delivered`); у покупок набор/PDF — `awaiting_payment → delivered`.
- Новые коды ошибок: `voice_unavailable`, `voice_cap`, `voice_in_progress`, `already_voiced`, `letter_too_long`, `group_closed`, `group_full`, `not_enough`, `already_built`, `no_credits`, `credit_not_allowed`.
- Откат: `MINIAPP_API_V2=0` скрывает все ручки раздела 7; данные (группы, обращения, покупки) остаются в хранилище.
