# Resonance

多周期、多信号源的**有状态事件关联引擎**。TradingView 的 Alert 是无状态的点事件（"某根 K 线收盘时某指标触发"），单条事件信息量低、噪声大。Resonance 把这些离散事件重建成内存中的状态，在**事件时间**上做跨周期、跨信号类型的关联，只有当多个独立证据同时成立时才产出信号，并把当时的行情证据（多周期 K 线图）一并推送到 Telegram，同时旁路归档到 dashboard。

```
TradingView Alerts ──► Webhook ──► 状态重建 ──► 关联匹配 ──► 门控 ──► 渲染 ──► Telegram
                                      │                              │
                                      └─ 缓存/窗口/心跳              └──► Dashboard 归档
```

## 核心设计

### 1. 事件时间，而不是墙上时间
所有时间判断基于事件自带的时间戳（K 线收盘时间 `event.ts`），不使用 `time.time()`。Webhook 到达有延迟、乱序、重试，用墙上时间会让 WARM、冷冻、窗口判断随网络抖动漂移。

### 2. 周期状态机：IN / WARM / OUT
每个 `(symbol, interval)` 维护超买/超卖两个方向的状态：

| 状态 | 含义 |
|------|------|
| `IN` | 当前指标值触发阈值（OB ≥ 40 / OS ≤ -40） |
| `WARM` | 已离开区间，但仍在 `warm_k × 周期时长` 内，仍参与关联（吸收"一根 K 线之差"的错位） |
| `OUT` | 其余情况 |

WARM 是**由时间戳推导**的派生状态，不依赖定时任务翻转，因此在乱序和重放下行为一致。

**心跳缺席检测（crypto）**：TV 只在进入区间时发 Alert，无法直接得知"何时离开"。`HeartbeatScheduler` 精准唤醒在每根 K 线收盘后 30 秒，检查该周期所有 crypto 标的是否收到了心跳，缺席即判定已离开区间并翻转状态。唤醒点按 K 线收盘时刻计算，而不是固定间隔轮询。

### 3. 信号关联：多个独立证据同时成立才推送
单一信号不推，推的是**组合**。各路信号作为独立证据，在各自的规则表里与 OB/OS 状态做匹配：

| 信号源 | 关联逻辑 | 规则位置 |
|--------|----------|----------|
| **共振** | 多周期同时 IN/WARM，匹配组合白名单；按最大周期路由到 Topic | `resonance_combinations.py`、`routing.yaml` |
| **Zone** | EborSR 支撑/阻力区触及的**瞬间**查询 OB/OS 快照：阻力配超买、支撑配超卖 | `zone_rules.ZONE_RULES` |
| **EMA 触及** | EMA21/55/200 触及 + 同级别 OB/OS IN + 均线排列 | `zone_rules.EMA200_RULES`、`ema_service.py` |
| **背离 / 波动预警** | 同周期处于 IN 才推；波动预警带到期时间（1.5 倍 K 线时长）续期 | `divergence_service.py`、`volatile_service.py` |
| **趋势标签** | 8 种瞬时标签触发后**反查**：OB/OS 状态、近期 Zone 触及、近期均线触及（窗口 = 5 根触发周期 K 线），至少一条配合才推 | `trend_rules.py`、`trend_service.py` |

Zone / EMA / 趋势标签都是**瞬时事件**，不像 OB/OS 有持续生命周期。因此无条件把它们写入触及缓存（`zone_touch_cache`、`ema_touch_cache`、`trend_label_cache`），不管当时是否推送；之后到来的其他信号可以反查这些缓存，实现"事后配合"。

### 4. 推送门控
- **冷冻**：按 `(symbol, 周期, 方向…)` 分类型冷冻（各类 4 小时），同一组合不重复轰炸
- **升级**：已推送组合新增更多 IN 周期时，标记 `‼️升级‼️` 重推
- **支配过滤**：同一 Topic 内，子集组合被父集组合压制
- **话题锁**：同一 Topic 的发送串行，保证文字与图片不被其他事件插队
- **消息原子化**：文字与图合并为单条消息（caption）；图片失败降级为纯文字

### 5. 衰竭追踪（ExhaustionService）
推送是信号，**入场时机**由后续价格行为确认。每次推送后注册一个 2 小时追踪窗口，后台每 180 秒拉取 3m K 线，依次执行已注册规则：

- 超买衰竭：3m EMA21 下穿 EMA200（一级）→ 下穿 EMA55（二级）
- 超卖衰竭：3m EMA21 上穿 EMA200 → 上穿 EMA55

规则实现 `ExhaustionRule` 接口，可动态注册/注销；`add_skip_filter` 可按上下文（如 1h Zone + 15m OB/OS）跳过追踪。命中后推送到入场 Topic（`TG_TOPIC_ENTRY`），并引用原信号。

### 6. 证据渲染
推送图由后端实时生成，不依赖 TradingView 截图：
- **数据源**：Binance Futures K 线，失败回落 yfinance（覆盖美股/大宗）
- **多图拼接**：按信号最大周期选 4 个周期（如 1D 信号 → 1D/4h/1h，4h 信号 → 1D/4h/1h/15m，其余 → 4h/1h/15m/3m），并发拉取、并发绘图、垂直拼接
- **图层**：EMA21/55/100/200（EMA200 需 ~1200+ 根预热以收敛）、Zone 色带、趋势标签标注、周期水印、美东时间标题；超买红 / 超卖绿
- **标题**：只在顶部子图保留，子图之间只留一条细缝，提升单屏信息密度

### 7. 重点关注与老五信号
- `laowu-listener` 容器用 **Telethon** 以用户账号监听老五 bot（Bot 之间无法直接互通），转发到 `/webhook/laowu`，解析后仅处理重点关注列表中的标的
- `/plan <Notion 链接>` 抓取老五每日"山寨关注"表（通过 Notion 公开页内部接口，非官方 API），把 4H / 1H 两句推荐原因写入图表标题，并覆盖重点关注列表
- **去重**：标的在重点关注列表时，消息**只发重点关注话题**，不再发原频道，避免一条消息占多个频道的注意力

### 8. 旁路能力
| 能力 | 说明 |
|------|------|
| AI 图表分析 | 4h 频道推送图交给 Claude 分析；每日成本超 $1 自动关闭 |
| 市场简报 | 美东每日定时，按时段（开盘前/盘中/盘后）生成，独立 Topic |
| 超买超卖快照 | 美东整点每 4 小时全量扫描 |
| 推送统计 | 每日推送次数、token 成本统计，UTC 0 点汇总 |
| Dashboard 归档 | 信号（含结构化 `elements` 与图片字节）和老五推荐旁路 POST 到 resonance_dashboard，火忘模式，失败只记日志，绝不影响 Telegram 推送 |

## 架构

```
                        ┌─ /webhook/tradingview ─┬─ event=volatile ─────► VolatileService
                        │                        ├─ event=divergence ───► DivergenceService
TradingView ────────────┤                        ├─ type=ema*_interaction ► EmaService
                        │                        ├─ type=zone_interaction ► ZoneService
                        │                        └─ 其他（OB/OS 数值）──► ResonanceService (+ Zone 反查)
                        └─ /webhook/trend ───────────────────────────────► TrendService

老五 bot ─► laowu-listener(Telethon) ─► /webhook/laowu ─► LaoWuService（重点关注过滤）

                    ┌───────────── AppState（内存：cache / gate / 触及缓存 / 追踪窗口）
各 Service ─────────┤
                    └───────────── send_with_chart ─► 渲染 ─► Telegram（含重点关注重定向）
                                                        └─► dashboard_client（旁路归档）

后台任务：HeartbeatScheduler · ExhaustionService · ObosScanService · DailySummaryService · MarketBriefingService · Telegram 指令轮询
```

```
app/
├── main.py                       # FastAPI 入口、webhook 分发、后台任务生命周期
├── config.py                     # pydantic-settings + universe/routing YAML（热读）
├── domain/
│   ├── models.py                 # 事件/快照/追踪窗口等数据模型
│   └── rules.py                  # OB/OS 判定纯函数
├── services/
│   ├── resonance_service.py      # 多周期共振主流程
│   ├── resonance_combinations.py # 组合白名单、生命周期匹配（首次/重推/升级）
│   ├── router.py                 # Topic 路由
│   ├── zone_service.py / zone_rules.py
│   ├── ema_service.py            # EMA21 / 55 / 200 触及
│   ├── divergence_service.py
│   ├── volatile_service.py
│   ├── trend_service.py / trend_rules.py
│   ├── exhaustion_service.py     # 衰竭追踪（可插拔规则）
│   ├── heartbeat_scheduler.py    # 心跳缺席检测
│   ├── laowu_service.py          # 老五信号 → 重点关注
│   ├── tg_command_handler.py     # Telegram 指令（inline keyboard）
│   ├── chart_analysis.py / prompts.py  # Claude 图表分析
│   ├── market_briefing_service.py / obos_scan_service.py / daily_summary_service.py
├── infra/
│   ├── store.py                  # AppState：全部运行态
│   ├── chart.py                  # K 线获取、绘图、拼接、send_with_chart
│   ├── stats.py / utils.py / logger_config.py
└── adapters/
    ├── tv_parser.py              # TradingView payload 解析
    ├── tg_client.py              # Telegram Bot 客户端
    ├── claude_client.py
    ├── dashboard_client.py       # dashboard 旁路归档
    ├── laowu_parser.py           # 老五消息解析
    └── notion_plan.py            # Notion 每日关注表抓取
scripts/laowu_listener.py         # Telethon 监听器（独立容器运行）
config/{universe,routing}.yaml    # 监控品种与周期、组合路由（热更新）
```

## 接口

| 路径 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `POST /webhook/tradingview` | 按 payload 判别：`event=volatile` / `event=divergence` / `type=ema_interaction`、`ema55_interaction`、`ema200_interaction` / `type=zone_interaction` / 其余视为 OB/OS 数值 |
| `POST /webhook/trend` | 趋势过滤器标签（8 位二进制，K 线收盘触发） |
| `POST /webhook/laowu` | `{"text": "...", "ts": 1700000000}`，由 laowu-listener 调用 |

**OB/OS 信号**
```json
{ "symbol": "BTCUSDT.P", "interval": "60", "value": -42.5, "timenow": "2026-01-13T00:01:00Z" }
```

**Zone 触及（EborSR）**
```json
{ "type": "zone_interaction", "ticker": "BTCUSDT.P", "interval": "60",
  "top": 69320, "bot": 69180, "role": "R", "close": 68370, "ts": 1736726460 }
```

**趋势标签**
```json
{ "symbol": "AAVEUSDT.P", "interval": "240", "timenow": "2026-01-13T00:00:00Z",
  "labels": "0 0 0 0 0 1 0 0" }
```
标签顺序固定：顺势多 / 顺势空 / 回调 / 反弹 / 区间反弹 / 区间回落 / 潜在顶部 / 潜在底部。

`interval` 使用 TradingView 分钟数（`60`=1h，`240`=4h，`15`=15m）；`role`：`R`=阻力，`S`=支撑。symbol 的 `.P` 后缀在解析时去除。

## Telegram 指令

仅响应 `TG_OWNER_CHAT_ID` 的私聊。`/start` 或 `/menu` 打开 inline keyboard 主菜单。

| 指令 | 说明 |
|------|------|
| `/cache <symbol>` | 各周期 IN/WARM/OUT |
| `/combo <symbol>` | 当前 active 共振组合 |
| `/zone <symbol>` | Zone 触及 warm 状态 |
| `/divergence <symbol>` | 各周期上次背离时间 |
| `/tracking` | 当前衰竭追踪窗口 |
| `/scan [interval]` | 全量扫描超买/超卖 |
| `/universe` `/check` `/add` `/remove` | 监控品种管理（`/add`、`/remove` 写 `universe.local.yaml`，热更新） |
| `/stats` | 今日推送统计 |
| `/analysis on\|off` | AI 图表分析开关 |
| `/briefing on\|off\|now` | 市场简报开关/立即推送 |
| `/focus [symbols…]` `/focus_clear` | 设置/清空重点关注 |
| `/plan <Notion 链接>` | 解析老五每日山寨关注：设置重点关注 + 图表推荐原因 + 归档 dashboard |

`/focus` 与 `/plan` 互相覆盖：`/plan` 把重点关注列表设为这批标的（补 `USDT`）；`/focus` 设置新列表时清空 `/plan` 的推荐原因，避免标题里残留昨天的理由。

## 部署

`docker compose up -d --build` 启动三个容器：

| 容器 | 职责 |
|------|------|
| `resonance` | FastAPI 主程序（uvicorn :8000） |
| `nginx-proxy` | 反向代理，对外 :80 |
| `laowu-listener` | Telethon 监听老五 bot，转发到 `resonance:8000/webhook/laowu`（容器内网直连） |

### 首次部署

```bash
git clone <repo_url> && cd resonance
cp .env.example .env          # 填写配置，见下
docker compose build
docker compose run --rm laowu-listener   # 仅首次：交互登录 Telegram（手机号 + 验证码），成功后 Ctrl+C
docker compose up -d
```

登录会话保存在 `./data/laowu_session/`（已 gitignore），重启和重建镜像不会丢。会话丢失后需重新登录一次，否则容器会因等待验证码反复重启。

### 更新

```bash
git pull
docker compose up -d --build
```

| 改动 | 操作 |
|------|------|
| `app/`、`requirements.txt`、`Dockerfile`、`docker-compose.yml` | `git pull` + `docker compose up -d --build` |
| `scripts/laowu_listener.py` | `git pull` + `docker compose restart laowu-listener`（脚本为挂载，无需重建） |
| `.env` | `docker compose up -d` |
| `config/*.yaml` | 仅 `git pull`，热读无需重启 |

### 本地运行

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
pytest
```

## 配置

### .env

| 变量 | 说明 |
|------|------|
| `TG_BOT_TOKEN` `TG_CHAT_ID` `TG_OWNER_CHAT_ID` | Bot 凭证、推送群、指令私聊 |
| `TG_TOPIC_US/DAY/4H/1H/15MIN/PRICE/MAIN/SUMMARY/ENTRY` | 各类推送的 Topic（message_thread_id） |
| `TG_TOPIC_FOCUS` | 重点关注 Topic。**为 0 时不启用重定向**，老五信号也无法投递 |
| `TG_API_ID` `TG_API_HASH` `LAOWU_BOT_ID` | laowu-listener：Telegram 用户 API 与老五 bot 的 user_id |
| `DASHBOARD_PUSH_URL` `DASHBOARD_PUSH_TOKEN` | dashboard 归档地址（…/signals）与共享密钥；留空则关闭归档 |
| `ANTHROPIC_API_KEY` `CLAUDE_MODEL` | AI 分析与简报；留空则关闭 |

`.env` 修改后需重启对应容器。完整变量与默认值见 `app/config.py`、`.env.example`。

### universe.yaml
监控品种及每个品种允许参与计算的周期，**热更新**：保存后下一条事件即生效。`universe.local.yaml`（gitignore）是服务器本地覆盖层，Telegram 的 `/add`、`/remove` 改的就是它，不会改动版本库里的 `universe.yaml`。

```yaml
symbols:
  BTCUSDT:
    intervals: [1W, 1D, 4h, 1h, 15m, 5m, 3m, 30s]
```

### 规则表
共振组合白名单、Zone/EMA/趋势反查规则都是代码中的显式表（见上方"信号关联"），而不是隐式逻辑，调整规则就是改表。

## 运行约束

- **状态全部在内存中**。重启后 IN/WARM 状态、触及缓存、冷冻记录、追踪窗口、`/focus`、`/plan` 全部清空；OB/OS 状态约需几根 K 线自然恢复。**每次部署重启后，需重新发一次 `/plan`**
- WARM、冷冻、窗口均依赖事件时间戳，Webhook 的时间字段必须是 K 线收盘时间
- `/plan` 依赖 Notion 公开页内部接口与表结构（表名「山寨关注」，列名「标的」「关注原因」「简述」），页面改版会导致解析失败（回复"抓取失败"，详见日志）
- dashboard 归档、AI 分析、市场简报均为旁路，失败不影响主推送链路
