
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