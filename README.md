# Resonance

接收 TradingView Webhook 信号，检测多周期超买超卖共振以及支撑阻力区域触及，推送至 Telegram。

## 功能

**多周期共振（Resonance）**
- 接收各周期的指标数值，判断当前是否处于超买（≥40）或超卖（≤-40）区域
- 多个周期同时触发时，按组合白名单匹配，推送至对应 Telegram Topic
- 支持 WARM 状态：周期刚离开超买超卖区域后仍短暂参与共振计算
- 支持升级推送：已推送组合新增更多周期时标记 ‼️升级‼️

**支撑阻力区域触及（Zone）**
- 接收 EborSR 指标推送的区域触及事件
- 在触及瞬间查询各周期超买超卖状态，按规则匹配后推送
- 规则：阻力区配合超买，支撑区配合超卖

## 架构

```
TradingView Webhook
       │
       ▼
  FastAPI /webhook/tradingview
       │
       ├─ type=zone_interaction ──► ZoneService ──► Telegram
       │
       └─ 超买超卖信号 ──────────► ResonanceService ──► Telegram
```

```
app/
├── main.py                    # FastAPI 入口
├── config.py                  # 配置（pydantic-settings + YAML）
├── domain/
│   ├── models.py              # TvEvent, ZoneEvent, ResonanceSnapshot 等
│   └── rules.py               # 超买超卖判定纯函数
├── services/
│   ├── resonance_service.py   # 多周期共振主逻辑
│   ├── resonance_combinations.py  # 组合白名单与匹配
│   ├── zone_service.py        # 区域触及主逻辑
│   ├── zone_rules.py          # 区域匹配规则
│   └── router.py              # Topic 路由
├── infra/
│   ├── store.py               # 内存状态（AppState）
│   └── logger_config.py       # 日志轮转
└── adapters/
    ├── tv_parser.py           # TradingView payload 解析
    └── tg_client.py           # Telegram Bot 客户端
config/
├── universe.yaml              # 监控品种与允许周期
└── routing.yaml               # 组合最大周期 → Topic 映射
```

## 安装与部署

### 方式一：Docker（推荐）

**依赖**：Docker、Docker Compose

```bash
# 1. 克隆项目
git clone <repo_url>
cd resonance

# 2. 创建 .env（见下方配置说明）
cp .env.example .env  # 或手动创建

# 3. 启动
docker compose up -d --build

# 4. 验证
curl http://localhost:80/health
```

### 方式二：本地运行

**依赖**：Python 3.11+

```bash
# 1. 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 创建 .env（见下方配置说明）

# 4. 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 服务器更新

```bash
git pull
docker compose up -d --build
```

## 配置说明

### .env

```env
TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=your_chat_id
TG_TOPIC_WEEK=111
TG_TOPIC_DAY=222
TG_TOPIC_4H=333
TG_TOPIC_1H=444
TG_TOPIC_15MIN=555
TG_TOPIC_PRICE=666
```

> `.env` 修改需重启服务生效。

### universe.yaml

定义监控的品种和每个品种允许参与计算的时间周期。**支持热更新**，直接修改文件保存后下一条信号进来即生效，无需重启。

```yaml
symbols:
  BTCUSDT:
    intervals: [1W, 1D, 4h, 1h, 15m, 5m, 3m, 30s]
```

### 共振组合白名单

在 `app/services/resonance_combinations.py` 的 `ALLOWED_COMBINATIONS` 中维护，每个组合映射到对应的 Telegram Topic。

### 区域触及规则

在 `app/services/zone_rules.py` 的 `ZONE_RULES` 中维护：

| Zone 周期 | 配合的 ob/os 周期 |
|-----------|------------------|
| 4h | 4h |
| 4h | 1h |
| 4h | 15m |
| 1h | 1h |
| 1h | 15m |

## TradingView Webhook 格式

Webhook URL：`http://your-server/webhook/tradingview`

**超买超卖信号**
```json
{
  "symbol": "BTCUSDT.P",
  "interval": "60",
  "value": -42.5,
  "timenow": "2026-01-13T00:01:00Z"
}
```

**区域触及信号（EborSR）**
```json
{
  "type": "zone_interaction",
  "ticker": "BTCUSDT.P",
  "interval": "60",
  "top": 69320,
  "bot": 69180,
  "role": "R",
  "close": 68370,
  "ts": 1736726460
}
```

`interval` 使用分钟数：`60`=1h，`240`=4h，`15`=15m。`role`：`R`=阻力，`S`=支撑。

## 注意事项

- 所有状态存储在内存中，服务重启后清空，约需几根 K 线自然恢复
- `.env` 修改需重启生效；`universe.yaml` / `routing.yaml` 支持热更新
