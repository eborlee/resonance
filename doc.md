
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