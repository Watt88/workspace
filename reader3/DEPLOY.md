# Свой домен для reader3: домашний компьютер и Cloudflare Tunnel

Читалка работает у вас дома, а Cloudflare Tunnel делает её доступной по адресу вроде `https://reader.example.ru`:

- HTTPS-сертификат выдаётся автоматически и бесплатно;
- открывать порты на роутере и иметь «белый» IP не нужно;
- адрес вашего дома снаружи не виден.

Ограничение одно: читалка доступна, пока включён компьютер.

```
телефон ──HTTPS──▶ Cloudflare ──туннель──▶ cloudflared на вашем компьютере ──▶ reader3
```

> **Пароль обязателен.** Без `READER3_PASSWORD` читалка отклоняет все запросы, пришедшие через туннель. Иначе любой, кто узнает адрес, сможет удалить ваши книги и тратить ваш ключ Claude API. Выберите длинный пароль, например из 4–5 случайных слов.

---

## Вариант 0. Без домена, за 1 минуту

Подходит, чтобы попробовать. Аккаунт Cloudflare не нужен.

1. Установите `cloudflared`: [инструкция Cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/). Коротко: на macOS `brew install cloudflared`, на Windows `winget install --id Cloudflare.cloudflared`.
2. Запустите читалку с паролем:
   ```bash
   READER3_PASSWORD='ваш-длинный-пароль' uv run server.py
   ```
3. Во втором окне терминала откройте туннель:
   ```bash
   cloudflared tunnel --url http://localhost:8123
   ```
   Программа напечатает адрес вида `https://random-words.trycloudflare.com`. Откройте его на телефоне.

Минус в том, что адрес меняется при каждом запуске. Для постоянного адреса нужен свой домен (варианты ниже).

---

## Шаг 1. Домен

Если домена нет, купите его. Это примерно 200–1000 ₽ в год.

- **Домены .ru и .рф** продают [REG.RU](https://www.reg.ru/), [RU-CENTER](https://www.nic.ru/) и другие российские регистраторы. После покупки DNS нужно передать в Cloudflare (шаг 2).
- **Домены .com, .org, .net и другие международные** выгоднее брать у [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/). Там они продаются по себестоимости, и шаг 2 делать не нужно.

Отдельный домен для читалки не обязателен: можно использовать поддомен существующего, например `reader.ваш-сайт.ru`.

## Шаг 2. Подключить домен к Cloudflare

1. Зарегистрируйтесь на [dash.cloudflare.com](https://dash.cloudflare.com/). Бесплатного тарифа Free достаточно.
2. Нажмите **Add a domain** (или **Add site**), введите домен и выберите план **Free**.
3. Cloudflare покажет два NS-сервера, например `anna.ns.cloudflare.com` и `bob.ns.cloudflare.com`. Укажите их в панели регистратора вместо текущих. В REG.RU это делается в разделе «Домены» → ваш домен → «DNS-серверы и управление зоной».
4. Подождите, пока Cloudflare пришлёт письмо, что домен активен. Обычно это занимает от 15 минут до нескольких часов.

## Шаг 3. Создать туннель

1. В панели Cloudflare откройте **Zero Trust** → **Networks** → **Tunnels** и нажмите **Create a tunnel**.
2. Выберите тип **Cloudflared**, назовите туннель, например `reader3`, и сохраните.
3. Cloudflare покажет команду установки с длинным **токеном** после `--token`. Скопируйте только токен.
4. На следующем шаге добавьте адрес. Он называется **Public Hostname**, в новых версиях панели — **Published application route**:
   - **Subdomain**: `reader`, или оставьте пустым, чтобы использовать корень домена;
   - **Domain**: ваш домен;
   - **Service**: `HTTP` и адрес
     - `reader3:8123`, если запускаете через Docker (вариант А);
     - `localhost:8123`, если запускаете без Docker (вариант Б).

## Шаг 4А. Запуск через Docker (рекомендуется)

Нужен [Docker Desktop](https://www.docker.com/products/docker-desktop/) на Windows или macOS либо Docker на Linux. Читалка и туннель будут запускаться сами при включении компьютера.

```bash
git clone -b claude/reader3-upgrade https://github.com/Watt88/workspace
cd workspace/reader3
cp .env.example .env
```

Откройте `.env` и заполните:

```ini
READER3_PASSWORD=ваш-длинный-пароль
TUNNEL_TOKEN=токен-из-шага-3
ANTHROPIC_API_KEY=sk-ant-...   # необязательно, для чата о книге
```

Запустите:

```bash
docker compose up -d --build
```

После этого:
- читалка открывается по адресу `https://reader.ваш-домен` и, на этом компьютере, по `http://localhost:8123`;
- книги и прогресс хранятся в папке `library/` рядом с проектом;
- обновить читалку: `git pull && docker compose up -d --build`;
- посмотреть журнал: `docker compose logs -f`, остановить: `docker compose down`.

## Шаг 4Б. Запуск без Docker

1. Установите `cloudflared` (ссылка в варианте 0) и зарегистрируйте туннель как системную службу, которая стартует вместе с компьютером:
   ```bash
   # macOS / Linux
   sudo cloudflared service install ТОКЕН-ИЗ-ШАГА-3
   # Windows (PowerShell от имени администратора)
   cloudflared.exe service install ТОКЕН-ИЗ-ШАГА-3
   ```
2. Запустите читалку с паролем:
   ```bash
   cd workspace/reader3
   READER3_PASSWORD='ваш-длинный-пароль' ANTHROPIC_API_KEY=sk-ant-... uv run server.py
   ```
   В Windows (PowerShell):
   ```powershell
   $env:READER3_PASSWORD='ваш-длинный-пароль'; uv run server.py
   ```

Чтобы читалка сама запускалась на Linux, можно создать службу systemd `/etc/systemd/system/reader3.service`:

```ini
[Unit]
Description=reader3
After=network-online.target

[Service]
WorkingDirectory=/home/ВЫ/workspace/reader3
Environment=READER3_PASSWORD=ваш-длинный-пароль
Environment=ANTHROPIC_API_KEY=sk-ant-...
ExecStart=/home/ВЫ/.local/bin/uv run server.py
Restart=always
User=ВЫ

[Install]
WantedBy=multi-user.target
```

Затем включите её командой `sudo systemctl enable --now reader3`. На macOS и Windows проще использовать вариант А с Docker.

## Шаг 5. На телефоне

1. Откройте `https://reader.ваш-домен` и введите пароль. Устройство запомнит вход на полгода.
2. Добавьте читалку на главный экран, и она будет открываться как приложение:
   - iPhone и iPad: Safari → «Поделиться» → «На экран „Домой“»;
   - Android: Chrome → ⋮ → «Добавить на главный экран».

---

## Безопасность

- После смены пароля в `.env` выполните `docker compose up -d` (или перезапустите сервер). Все устройства выйдут из аккаунта, и войти можно будет только с новым паролем.
- После 10 неверных паролей подряд вход с этого IP блокируется на 10 минут.
- Дополнительная защита: в Cloudflare Zero Trust можно включить **Access** для адреса читалки. Тогда перед страницей входа Cloudflare будет спрашивать код, присланный на вашу почту. Это бесплатно до 50 пользователей.
- Если включён чат с Claude, каждый вопрос тратит средства с вашего API-ключа. Лимит расходов задаётся в [консоли Anthropic](https://console.anthropic.com/).

## Если что-то не работает

| Симптом | Что проверить |
|---|---|
| Ошибка Cloudflare 1033 / «Tunnel error» | Не запущен `cloudflared`: `docker compose ps` или `cloudflared tunnel list` |
| Ошибка 502 Bad Gateway | Не запущена читалка или в шаге 3 указан не тот адрес сервиса (`reader3:8123` для Docker, `localhost:8123` без него) |
| «Доступ из интернета без пароля запрещён» | Не задан `READER3_PASSWORD` |
| Домен не открывается сразу после покупки | NS-серверы ещё не обновились. Подождите письма от Cloudflare (шаг 2) |
