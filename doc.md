
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
1. **OB/OS 状态**：读当前缓存，查相关周期是否处于 IN 或 WARM
2. **近期区域触及**：查 `zone_touch_cache`，时间窗口 = 5 根触发周期的 K 线
3. **近期均线触及**：查均线触及缓存（待确认是否已有），同样 5 根 K 窗口

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

**Telegram topic 路由：** 待确认（建议按触发周期路由，4h→TG_TOPIC_4H 等）

---

### 开发顺序建议

1. `app/domain/models.py` — 新增 `TrendEvent` 数据模型
2. `app/adapters/tv_parser.py` — 新增 `parse_trend_payload()`，解析 labels 字段
3. `app/infra/store.py` — AppState 新增 `trend_label_cache` + `record_trend_label()` / `get_recent_trend_labels()`
4. `app/services/trend_service.py`（新建）— `TrendService.handle_event()`，执行反查 + 推送
5. `app/main.py` — 新增 `/webhook/trend` endpoint，初始化 TrendService
6. `app/infra/chart.py` — `_draw_chart` 新增 `trend_annotations` 参数，画标注

### 尚待确认

- Telegram topic 路由规则（按触发周期？还是固定一个 topic？）
- 均线触及是否有现成缓存可用（EMA service 目前只缓存冷冻时间，没有触及事件缓存）
- Idea 1 标注的视觉样式（竖线？三角形？颜色区分标签类型？）

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