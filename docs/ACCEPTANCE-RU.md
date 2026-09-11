# Приёмка этапа «терминал + сцена» (2026-09-10)

Все проверки выполнены реальным запуском; команды и артефакты указаны. Коммиты: `f7800c0` (контракт + TUI), `1779be7` (web), `b057537` (приёмка/демо). Точка возврата к предыдущей версии: тег `v0.1-stage3`.

| Проверка | Условие | Результат | Доказательство |
|---|---|---|---|
| Реальный терминал | PTY, 120×36 и 180×50, выбор/поиск/выход, без браузера | Выполнено | `scripts/pty_check.py --size 120x36` и `--size 180x50`: alternate screen вход/выход, курсор восстановлен, бренд и строки нарисованы, `q` завершает; `--colors 256` тоже OK; логи `demo/tui/pty-*.log` |
| Общий replay | Одинаковый session/time в TUI и web: пара, distinct wallets, sequences, режим | Выполнено | `tests/test_shared_session.py`: один сервер, `seek` с web-стороны виден TUI (clock 1400), evidence TUI `2 WALLETS · 3 sequences` = `/api/edge` `wallets_main=2, sequences_total=3`, `←` в TUI двигает clock для web (1340). В записи: одна session `S-a0cfdf` в обеих поверхностях |
| Event correctness | Повторный snapshot не меняет count; pause не добавляет событий; seek не проигрывает историю как fresh; >25 событий между polls не теряются | Выполнено | `tests/test_contract.py::test_events_cursor_pagination_loses_nothing` (60 событий страницами по 20, без потерь и дублей; повторный опрос даёт 0), `::test_events_history_after_seek_is_not_marked_new`; `web/e2e/web.spec.ts` «pause adds no events…» и «play produces pulses that stop on pause; seek does not replay history as fresh»; `tests/test_tui.py::test_seek_rebuilds_history_without_marking_it_fresh` |
| Роли чисел | 2 sequences одного wallet → wallets=1, sequences=2; адрес на двух рёбрах не удваивается у узла | Выполнено | `tests/test_contract.py::test_one_wallet_two_sequences_counts_one_wallet`, `::test_node_unique_wallets_do_not_double_count_across_edges` (`in_edge_wallet_sum` vs `in_unique_wallets`); в UI подписи «edge-wallet sums can count one address on several edges» |
| Сцена | Три уровня глубины, параллакс/перекрытия, камера приходит к событию, labels пары не перекрываются | Выполнено | WebGL (three.js): 3D layout с z-полосами (`layout3d.ts`), перспектива + fog, пролёт A → вдоль маршрута → B → evidence (`Scene3D.tsx`); `web.spec.ts` проверяет `viewState=evidence`, caption с вычисленным count и непересечение `.lbl.big.a/.b`; кадры `demo/v2/out/frame-web-{overview,pulses,follow,evidence,evidence-rows}.png` |
| Палитра | Чёрный/красный/нейтральный текст в TUI и web; кислотные цвета не перенесены; ошибка отличается от события | Выполнено | Токены `#050505/#0D0A0A/#351419/#FF3344/#260D12/#F2F2F2/#A3A3A3/#747474` в `web/src/styles.css` и `stampede/tui/app.py`; grep по lime/cyan/magenta/orange/зелёному пуст; ошибка — инверсия (красный фон, чёрный текст, `!`), события — красный маркер/линия на чёрном |
| Читаемость | На кадре 720×450 читаются имя, A→B, число, REPLAY/LIVE | Выполнено, проверено глазами | `demo/shots/16-web-v2-evidence-720x450.png`, `demo/v2/out/frame-web-evidence-720x450.png` |
| Ошибки | Provider/network error не заменяется mock; stale/unknown не зелёный; нет 1970 | Выполнено | `demo/v2/errors/*` (web и TUI с неверным ключом: инверсный блок, `LAST BLOCK ? UTC`, пустая лента); `tests/test_contract.py::test_interp_never_invents_1970`; `stampede repair-ts` исправил 1 819 строк с ts=0, `store.unknown_time_trades=0`; `connection: stale` при возрасте >60 с; live не рисует старую выборку до первого live-блока |
| Регрессия | 26 старых тестов зелёные; добавлены тесты контракта/часов/TUI/web; build и lint проходят | Выполнено | `pytest -q`: 42 passed (26 старых + 8 контракт + 7 TUI + 1 shared session); `npx playwright test`: 6 passed; `npm run build` и `npm run lint` без ошибок |
| Воспроизводимость | Свежая установка по README, сохранённые команды, повтор записи | См. `docs/RELEASE-CHECKLIST.md` | Команды записи в `docs/DEMO-V2.md`; `demo/v2/build.py` собирает mp4 из сырых записей; `SHA256SUMS.txt` |

## Проверка пользы на живой сети (2026-09-11)

`python scripts/bg.py serve-live -- .venv/bin/python -m stampede serve --mode live --port 8793`, через ~4 мин: блок 60 152 073, лаг 2.7 с, 66 тиков, 207 direct/clean последовательностей за последние 30 минут реального времени. `scripts/verify_live_sequences.py --n 6`: 6/6 новейших последовательностей подтверждены независимо через raw RPC (обе транзакции существуют, кошелёк реально отправил проданный токен и получил купленный по Transfer-логам, sell-блок раньше buy-блока, время заголовков совпадает с записанным). Вывод: `demo/v3/live/verify.json`, кадры TUI на live: `demo/v3/live/tui-live-*.png`. Топ ротаций на тот момент: `BEARLY·daed → SQUIDMIND·1c82` 6 кошельков, `AAPL·d4ec → FIRST·1d80` 5.

Найдено и исправлено по ходу: 502 тикера в базе принадлежат нескольким монетам (RBNHD ×28, MARIO ×13) — строки вида `MONARCH → MONARCH` были двумя разными монетами. Теперь такие тикеры получают суффикс адреса (`MARIO·43cb`) во всех выдачах API, TUI и web (тест `test_duplicate_tickers_are_disambiguated_by_address`).

## Что измерено по кадрам

- Web 1440×900: p50 8.3 мс / p95 10 мс на 120 Гц (реальный GPU, headed Chromium), 60 fps при vsync 60 в headless c Metal при dpr 1 и dpr 2; 260 рёбер / 178 узлов. Программный рендер SwiftShader (без GPU) даёт 15 fps — это не целевая конфигурация, приведено для контраста. Подробности: `docs/DEMO-V2.md`.
- TUI обновляется по событиям (poll 2 с), полный redraw не делается.

## Закрытые замечания аудита

1. Раздельные метрики: `/api/status.sample` (фиксированная выборка: 146 329 сделок), `/api/status.store` (вся база: 184 371, `unknown_time_trades`), видимое окно — в `graph.totals`. В rail «Scopes» подписаны отдельно.
2. `first_ts=0`: причина — интерполяция без якорей у ранних тиков live-хвоста; `Interp` теперь возвращает unknown вместо 0 (тест), `repair-ts` починил старые строки, live-хвост дотягивает заголовок блока или считает сделку unknown-time и не использует её в ротациях.
3. Уникальные кошельки узла считаются отдельно от суммы по рёбрам (`in_unique_wallets` / `in_edge_wallet_sum`).
4. Интерполированное время помечено `≈` в TUI и «(approx.)» в web; точность подтягивается при открытии evidence (`exact=1`).
5. Основной вес без ambiguous; scope только PONS v2; формулировка «same address, observed order of trades; not proof of money flow or shared ownership» в caption/легенде/TUI.

## Открытые вопросы (честно)

- Плотное ядро графа в OVERVIEW всё ещё читается как клубок при 260+ рёбрах; LOD режет только подписи, не рёбра. Следующий шаг — скрывать рёбра весом < 5 при дальней камере.
- Пролёт FOLLOW идёт мимо соседних узлов только если они есть рядом с маршрутом; параллакс виден, но «перекрытие ближними узлами» зависит от данных.
- Постоянный rAF-цикл: рендер не останавливается в простое (это помогло измерить frame times, но тратит CPU); нужен idle-stop.
- В headless-записи Playwright видеопоток ~25 fps; это свойство записи, не продукта.
- HyperSync-токен по-прежнему не подключён; ingest на Alchemy.


# Приёмка этапа «RADAR: андеррадар-ранеры» (2026-09-11)

| Проверка | Результат | Доказательство |
|---|---|---|
| Исследование R1 | Выполнено на 5 ч реальных данных (444 537 сделок, 9 596 монет, 38 059 direct/clean последовательностей, 44 graduation). База 5.30% монето-минут даёт ≥2× за 30 мин; inflow 20–39 кошельков за 10 мин — 21.6% (4.1×), ускорение ≥4 — 18.0% (3.4×), возраст 15–60 мин — 17.8%; правило «inflow ≥ 8, возраст < 1 ч, не +100% за 10 мин» — 20.2% (3.8×), с качеством кошельков ≥ 0.5 — 31.9% (6.0×), но медиана цены через 30 мин отрицательная (−25…−40%) | `docs/RESEARCH-RUNNERS.md`, `stampede/research/backtest.py`, `data/research-12h.sqlite` (5 ч, блоки 60018681–60198681) |
| Качество кошельков R2 | Walk-forward: 3 559 кошельков со score (≥3 известных исходов) и bot-флагами, импорт в рабочую базу `stampede wallet-scores --from` | `wallet_scores` в `data/stampede.sqlite`; radar показывает `quality` и `quality_known` |
| Модули M1–M4 | PONS lifecycle (launches/graduations из логов и on-demand), curve progress as-of, соцсети из calldata; GeckoTerminal; holders/dev по Transfer-истории; X mentions через twitterapi.io с бюджетом и кэшем | `stampede/context/*`, `tests/test_radar.py`; проверено на реальной монете (TruffleHog·7b08: threshold 8 090 USDG, progress 23%, dev продал 18.7%, X 0/22) |
| Radar API | `/api/radar` (score c разбором, пресеты, фильтры, кэш 4 с, один расчёт на clock), `/api/coin/{t}`, `/api/alerts` | `tests/test_radar.py` (4), `web/e2e/web.spec.ts` (radar/flow) |
| Web | RADAR (лидерборд, фильтры, пресеты, спарклайны, вспышки строк), FLOW (эго-сеть лентами, подписи без наложений), coin drawer, MAP c top-N потоков; клавиши 1/2/3, D, Esc | `web/src/components/{Radar,Flow,CoinDrawer}.tsx`, кадры `demo/v4/out/frame-*.png`, e2e 8 passed |
| TUI | Экран Radar (Tab), пресеты u/g/s/a, карточка монеты (Enter), `r` обновить контекст | `demo/v4/tui/b-radar-*.png`, `tests/test_tui.py` |
| Алерты | Правило v2 + журнал с исходами +30/+60 мин, macOS-уведомление по `--notify`; трек-рекорд честный (в записи 0/7 вверх через 30 мин при v1-правиле → правило пересобрано по бэктесту) | `/api/alerts`, кадр `demo/v4/out/frame-alerts.png` |
| Регрессия | pytest 46 passed; Playwright 8 passed; build/lint чистые | этот запуск |
| Демо v4 | 26.9 с walkthrough + 5 с cut, кадры, читаемость 720×450 проверена | `demo/v4/out/`, `docs/DEMO-V4.md` |

Ограничения и открытые вопросы: внешний контекст (X, GeckoTerminal, holders) в replay не запрашивается — цифры «now» не смешиваются с часами replay; X-поиск по тикеру-слову ловит посторонние твиты (снижено запросом `$SYM OR адрес OR (SYM AND robinhood/pons)`); в исследовательской базе не разрешены названия токенов (только адреса) — на бэктест не влияет; 5 ч — первая мера, не закон: скрипт пересчитывает всё на новом диапазоне.
