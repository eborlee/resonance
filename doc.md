
---

## 趋势过滤器集成（待开发）

### 背景

TradingView 指标"趋势过滤器"（百万Eric）输出 8 种瞬时标签信号，与现有 OB/OS（状态信号）有本质区别：
- **OB/OS**：过程状态信号，价格进入区域后持续保持 IN，有 WARM/OUT 生命周期
- **趋势标签**：瞬时事件信号，K 线收盘时触发一次，之后消失，无持续状态

### TV 端配置

**Webhook URL：**
```
http://8.209.204.201:80/webhook/trend
```

**Alert 消息 body（JSON）：**
```json
{
  "symbol": "{{ticker}}",
  "interval": "{{interval}}",
  "timenow": "{{timenow}}",
  "labels": "{{plot("顺势多")}} {{plot("顺势空")}} {{plot("回调")}} {{plot("反弹")}} {{plot("区间反弹")}} {{plot("区间回落")}} {{plot("潜在顶部")}} {{plot("潜在底部")}}"
}
```

**Labels 字段说明：**
- 空格分隔的 8 位二进制值，顺序固定：顺势多 / 顺势空 / 回调 / 反弹 / 区间反弹 / 区间回落 / 潜在顶部 / 潜在底部
- 示例："0 0 0 0 0 1 0 0" → 区间回落触发
- `{{ticker}}` 包含交易所后缀（如 `AAVEUSDT.P`），后端沿用现有去 `.P` 后缀逻辑
- `{{interval}}` 为 TV 格式（如 `240` 表示 4h），沿用现有 interval 映射逻辑
- K 线收盘触发，`timenow` = bar close 时间（如 14:15 = 14:00 那根 15m K 的收盘）

### 要开发的两个功能

---

#### Idea 1：图表标注层（视觉增强）

**逻辑：**
1. `/webhook/trend` 收到信号后，将标签记录缓存到 AppState（symbol / interval / 标签名 / 时间戳）
2. 现有各种推送生成图表时，查缓存，把近期出现的标签画到对应 K 线位置上
3. 标注方式：竖向虚线 + 文字标签（如"潜在底部"），标记在对应 K 线的 open time 处

**实现要点：**
- AppState 新增 `trend_label_cache: Dict[Tuple[str, str], List[Tuple[float, str]]]`，key=(symbol, interval)，value=[(ts, label_name), ...]，保留最近 N 条（如 20 条）
- `generate_chart` / `_draw_chart` 新增 `trend_annotations` 参数
- 用 `df.index.get_loc(timestamp, method='nearest')` 定位 bar 位置，`ax.axvline` + `ax.text` 画标注
- `send_with_chart` 调用时传入对应 symbol/interval 的缓存标签

---

#### Idea 2：趋势标签触发反查推送

**逻辑：**
趋势标签触发 → 反查以下配合信息 → 有配合才推送（无配合静默）：
1. **OB/OS 状态**：读当前缓存，查相关周期是否处于 IN 或 WARM（可跨周期，如 4h 触发时同时看 4h、1h）
2. **近期区域触及**：查 `zone_touch_cache`，**只查触发周期同级别**（4h 触发只查 4h 的区域触及，不跨周期），时间窗口 = 5 根触发周期的 K 线
3. **近期均线触及**：查 `ema_touch_cache`（新增，仿照 `zone_touch_cache`），**只查触发周期同级别**，同样 5 根 K 窗口

**`ema_touch_cache` 设计：**
- key = `(symbol, interval, period, role)`，区分 EMA21/55/200，与 zone 的 `(symbol, interval, role)` 结构一致
- value = `(ts, ema_value, close)`
- 在 `EmaService.handle_event()` 收到事件时无条件记录（不管后续是否匹配/冷冻），与 zone 的 Step 3 记录方式一致

**推送规则：**
- 8 个标签全部参与（不过滤）
- 必须至少匹配一条配合信息（OB/OS IN/WARM 或近期区域/均线触及），否则不推
- 所有周期（1D / 4h / 1h / 15m）均支持

**反查时间窗口（以触发标签的周期为基准，往前 5 根 K）：**

| 标签周期 | 窗口时长 |
|---------|---------|
| 1D | 5 天 |
| 4h | 20 小时 |
| 1h | 5 小时 |
| 15m | 75 分钟 |

**推送消息格式（示意）：**
```
🔻 AAVEUSDT 趋势信号
潜在底部 | 4h

配合:
🟢 4h 超卖 IN
🟢 1h 超卖 WARM

近期区域:
📍 69200 - 69500 (S) | 1D  [3小时前触及]
```

**Telegram topic 路由：** 统一推送到 `TG_TOPIC_MAIN`，不按触发周期区分

---

### 开发进度（已完成）

1. ✅ `app/domain/models.py` — 新增 `TrendEvent`、`TREND_LABEL_NAMES`（8 标签固定顺序）、`TREND_LABEL_SIDE`（标签方向表，反查 OB/OS 和图表上色共用）
2. ✅ `app/adapters/tv_parser.py` — 新增 `parse_trend_payload()`，解析 labels 字段；顺带修复两处历史 bug（见下方"顺带修复的 bug"）
3. ✅ `app/infra/store.py` — 新增：
   - `trend_label_cache` + `record_trend_label()` / `get_recent_trend_labels()`（Idea 1 用，保留最近 20 条）
   - `ema_touch_cache` + `update_ema_touch()`（仿照 `zone_touch_cache`，key=`(symbol, interval, period, role)`，Idea 2 均线反查用）
   - `trend_label_last_pushed` + `is_trend_label_in_cooldown()` / `record_trend_label_push()`（冷冻门控，统一 4 小时，与 zone/ema 保持一致）
4. ✅ `app/services/trend_rules.py`（新建）— `TREND_RULES`（触发周期→反查 OB/OS 周期）、`TREND_TOUCH_WINDOW_SECONDS`（5 根 K 反查窗口）、`TREND_COOLDOWN_SECONDS`（4h）；`TREND_LABEL_SIDE` 从这里改为从 `domain/models.py` 引入（避免 infra 反向依赖 services）
5. ✅ `app/services/trend_service.py`（新建）— `TrendService.handle_event()`：
   - 逐个 label 独立反查（同一事件可能同时触发多个标签，互不影响）
   - OB/OS 反查按标签方向只查一侧（超买/超卖），区域/均线反查不分方向、不分 R/S，只看时间窗口内有没有触及
   - 任一反查命中即推送，冷冻通过后发送到 `TG_TOPIC_MAIN`
6. ✅ `app/main.py` — 新增 `/webhook/trend` endpoint，初始化 `trend_svc`
7. ✅ `app/infra/chart.py` — Idea 1 图表标注：
   - `_draw_chart`/`generate_chart`/`generate_multi_chart`/`send_with_chart` 全链路新增 `trend_annotations` 参数
   - 视觉样式：竖虚线 + 水平文字；方向用色（复用 `TREND_LABEL_SIDE`）——偏空（超买）画在图上半部分，偏多（超卖）画在图下半部分
   - 多周期拼图时，标注只画在触发周期对应的那张子图上（`trend_annotations_iv` 参数控制）
   - 单条标注绘制失败只跳过该条，不影响 K 线图本身；失败信息会追加进推送文字（"⚠️ 趋势标注绘制失败"）
   - `trend_service.py` 自己的推送已接入：调用 `send_with_chart` 时传入 `state.get_recent_trend_labels(symbol, interval)`

**已确认的设计决策（供后续参考）：**
- 8 个标签方向表：顺势多→超卖，顺势空→超买，回调→超买，反弹→超卖，区间反弹→超卖，区间回落→超买，潜在顶部→超买，潜在底部→超卖
- OB/OS 反查周期规则（`TREND_RULES`）：1D→查1D+4h+1h，4h→查4h+1h，1h→查1h+15m，15m→只查15m
- 区域/均线反查：不分 R/S、不分上穿下穿，只要时间窗口内有触及记录就算命中（与现有 OB/OS+区域合成的处理方式一致）
- 冷冻时长：统一 4 小时，与 zone/ema200/ema55/ema21/波动预警一致
- Topic：统一 `TG_TOPIC_MAIN`，不按周期区分，不做美股 `TG_TOPIC_US` 覆盖

**顺带修复的 bug（与趋势过滤器功能本身无关，开发过程中发现）：**
- `routing.yaml`/`router.py` 的 `1w` 与 `universe.yaml`/`config.py`/`tv_parser.py` 的 `1W` 大小写不一致，导致周线共振信号的 `max_interval` 判断和 topic 路由失效（`rank_of("1W")` 查不到小写 key，返回 -1）。已统一改成大写 `1W`，同步修了 `test_router.py`/`test_tv_parser.py`。
- `tv_parser.py` 的 `INTERVAL_MAP` 里 `"W"` 曾被误映射到 `"1D"`（应为 `"1W"`），已修正。
- `ema_service.py`/`zone_service.py` 里记录触及缓存的调用补上了 try/except，避免缓存写入异常连带影响原有推送逻辑。

### 尚待完成 / 待确认

- **Idea 1 集成范围**：目前只有 `trend_service.py` 自己的推送会显示趋势标注；zone/ema/resonance/divergence/volatile 等其他现有推送服务尚未接入 `trend_annotations`（doc 原意是"现有各种推送生成图表时都查缓存画标注"，这部分还没做，需要确认是否要在这几个服务里也接入）
- 均线触及缓存（`ema_touch_cache`）目前只在 `EmaService.handle_event()` 里写入，还没有实际验证过反查逻辑在真实数据下的效果（缺集成测试）
- 未写自动化测试（`trend_service.py`、`parse_trend_payload`、`ema_touch_cache` 目前都没有对应的 pytest 用例）
- Telegram 里没有真实验证过 `/webhook/trend` 端到端流程（webhook → 反查 → 推送 → 图表标注）

---

待新增：
- tg外部命令
    - 一键重置缓存
    - 缓存查看
    - universe查看
    - 命令管理universe
- 日志优化
- universe完善
- config梳理优化
- 推送文本优化
- 波动预警反向合成（ob/os 事件反查波动预警状态）
    - 当前只有波动预警触发时检查 ob/os，存在最多 1 根 K 线的延迟（1h 延迟 1h，4h 延迟 4h）
    - 可在 ob/os 信号进入 IN 时，顺手检查 is_volatile_active，若 active 则立即推送
    - 冷冻共用同一套 (symbol, interval, side) key，不会重复推
    - 待确认：检查哪些 volatile interval、推送用哪个 topic


docker compose up -d --build

检查配置是否支持热更新
curl -X POST http://8.209.204.201:80/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "AAVEUSDT.P",
    "interval": "1h",
    "value": -55,
    "timenow": "2026-01-13T00:01:00Z"
  }'