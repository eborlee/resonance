# Resonance — 多周期共振信号推送机器人

## 安全规范（Vibe Coding Rules）

> 以下规则强制执行，不得以任何理由绕过。

1. **只读当前项目**：只允许读取本项目目录（`/Users/admin/Documents/coding/quant/resonance/`）内的文件，禁止访问项目外的任何路径。
2. **禁止删除文件**：禁止执行任何删除文件/目录的操作（`rm`、`rmdir`、`shutil.rmtree` 等）。
3. **禁止破坏性 git 操作**：禁止 `git reset --hard`、`git push --force`、`git clean -f`、`git branch -D` 等不可逆操作，除非用户明确要求并二次确认。
4. **禁止修改基础设施配置**：不得修改 `Dockerfile`、`docker-compose.yml`、nginx 配置，除非用户明确指示。
5. **写文件前必须先读**：修改任何现有文件前，必须先用 Read 工具读取其内容，禁止盲写覆盖。
6. **不主动 push 远程**：禁止在未经用户明确要求的情况下执行 `git push`。
7. **不执行网络请求**：禁止在代码之外主动发起对外 HTTP 请求（调试、测试脚本除外，且须告知用户）。
8. **变更范围最小化**：只修改任务所需的代码，不顺手重构、不添加未要求的功能、不删除未提及的逻辑。

## 项目概述

接收 TradingView Webhook 推送的指标数值，检测多个时间周期同时进入超买/超卖区域的"共振"状态，并将信号推送到 Telegram 对应的 Topic。

**技术栈：** Python 3.11, FastAPI, pydantic-settings, PyYAML, Docker + Nginx

---

## 核心概念

### Side（方向）
- `OVERBOUGHT`：超买（指标 >= OB_LEVEL，默认 40）
- `OVERSOLD`：超卖（指标 <= OS_LEVEL，默认 -40）

### LevelState（周期状态）
- `IN`：当前值触发阈值
- `WARM`：当前不在区内，但在最近 `warm_k * interval_seconds` 秒内曾触发（热余温）
- `OUT`：既不在 IN 也不在 WARM

### 组合（Combination）
定义在 `app/services/resonance_combinations.py` 的 `ALLOWED_COMBINATIONS` 白名单中。只有符合白名单的周期组合才会推送。组合按最大周期路由到不同 Telegram Topic。

### 升级（Upgrade）
已推送组合的基础上，新增了处于 IN 状态的周期时标记为 `‼️升级‼️`。

---

## 代码结构

```
app/
  main.py                          # FastAPI 入口，/webhook/tradingview
  config.py                        # Settings（pydantic）+ load_universe/load_routing
  domain/
    models.py                      # Side, LevelState, IntervalSignal, TvEvent, ResonanceSnapshot
    rules.py                       # classify_for_side, build_snapshot（纯函数）
    fsm.py                         # ⚠️ 废弃，保留备查
  services/
    resonance_service.py           # ResonanceService：主流程（过滤→更新缓存→组合匹配→推送）
    resonance_combinations.py      # ALLOWED_COMBINATIONS, COMBINATION_ROUTING, match_combinations_with_lifecycle
    router.py                      # choose_topic_by_max_interval, max_interval, apply_min_interval_floor
  infra/
    store.py                       # AppState（内存缓存 + 推送门控）, IntervalCache, GateRecord
    logger_config.py               # 日志轮转配置
    utils.py                       # ts_to_utc_str 等工具
  adapters/
    tg_client.py                   # TelegramClient（发送消息）
    tv_parser.py                   # parse_tv_payload（解析 TV Webhook JSON）
config/
  universe.yaml                    # 监控的币种及允许的时间窗口
  routing.yaml                     # max_interval → topic 映射
tests/                             # pytest 测试
```

---

## 主流程（ResonanceService.handle_event）

1. **过滤 universe**：不在 `universe.yaml` 中的 symbol/interval 直接丢弃
2. **更新缓存**：`AppState.update_interval` 记录最新值，追踪 IN/OUT 切换并记录退出时间戳
3. **构建状态字典**：对每个允许的周期判断 IN/WARM/OUT（WARM 基于时间差）
4. **按 max_iv 匹配组合**：`match_combinations_with_lifecycle` 判断首次/重推/升级
5. **Dominance 过滤**：同一 topic 内，子集组合被父集组合压制（不推送）
6. **并发推送**：`asyncio.gather` 批量发 Telegram 消息

---

## 配置

### 环境变量（.env）
```
TG_BOT_TOKEN=...
TG_CHAT_ID=...
TG_TOPIC_WEEK=...
TG_TOPIC_DAY=...
TG_TOPIC_4H=...
TG_TOPIC_1H=...
TG_TOPIC_15MIN=...
TG_TOPIC_PRICE=...
```

### universe.yaml
定义监控标的和每个标的允许的时间窗口。`get_universe()` 每次请求时从文件热读，支持热更新。

### routing.yaml
定义 `max_interval_to_topic` 和 `max_interval_min_allowed` 映射。

---

## 开发注意事项

- `AppState` 是纯内存状态，重启后清空，WARM 状态依赖准确的事件时间戳（`event.ts`，即 K 线收盘时间），不用 `time.time()`
- `get_universe()` / `get_routing_rules()` 每次调用都重新读文件（热更新），有性能代价但当前可接受
- `domain/fsm.py` 已废弃，勿依赖
- 推送失败不会抛出异常（`return_exceptions=True`）
- `handle_raw_text_fallback`：TV 发来的价格穿越文本（含"穿过"）走 `TG_TOPIC_PRICE`

## 常用命令

```bash
# 启动
docker compose up -d --build

# 测试 webhook
curl -X POST http://localhost:80/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","interval":"1h","value":-55,"timenow":"2026-01-13T00:01:00Z"}'

# 运行测试
pytest
```

## 待做事项（doc.md）

- Telegram 外部命令（重置缓存、查看缓存、管理 universe）
- 日志优化
- config 梳理优化
- 推送文本优化

## Zone 功能未来开发计划

- **`is_zone_warm` 目前闲置**：`AppState.update_zone_touch` 已记录区域触及时间戳，`is_zone_warm` 方法也已实现（2根K线有效期），但 `ZoneService.handle_event` 里没有用到它。当前模型是"zone_interaction 到来时查一次 ob/os 快照"，zone 本身作为触发器不需要 warm。
  - 未来场景：ob/os 共振信号到来时，反查 `is_zone_warm` 判断近期是否触及过关键区域，实现"共振 + 区域"的反向结合逻辑。
- **zone payload 时间戳**：Pine Script 已加入 `ts` 字段（`timenow`），`parse_zone_payload` 通过 `parse_ts` 读取，与超买超卖事件时间戳对齐。
