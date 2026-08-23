# Low-Carbon Value Screener — Backend

低碳价值筛选器后端服务，基于 Flask 构建。

整合美股实时行情数据（TradingView）与年度碳排放数据（Bavest），让用户自定义财务阈值和环境阈值，筛选符合"低碳+价值"双重标准的投资标的。

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| Web 框架 | Flask 3.1 | 轻量级 Python Web 框架 |
| ORM | Flask-SQLAlchemy 3.1 | 数据库抽象层 |
| 数据库 | SQLite (dev) / PostgreSQL (prod) | 开发用 SQLite，生产可切换 |
| 跨域 | Flask-CORS 6.0 | 前后端分离 CORS 支持 |
| 缓存 | Redis | 行情数据 5 分钟缓存（生产） |
| 迁移 | Flask-Migrate (Alembic) | 数据库版本管理 |

## 项目结构

```
low-carbon-backend/
├── app/
│   ├── __init__.py          # 应用工厂 (create_app)
│   ├── config.py            # 配置管理 (Dev/Prod/Test)
│   ├── extensions.py        # Flask 扩展实例
│   ├── models/              # 数据模型
│   │   ├── company.py       # 公司表
│   │   ├── financial_metric.py  # 财务指标表
│   │   ├── carbon_emission.py  # 碳排放数据表
│   │   └── preset_template.py  # 预设筛选模板表
│   ├── routes/              # API 路由
│   │   ├── screener.py      # /api/screener/*
│   │   └── stock.py         # /api/stock/*
│   ├── services/            # 业务逻辑层
│   │   ├── tradingview_service.py  # TradingView 行情服务
│   │   ├── carbon_service.py        # 碳排放数据服务
│   │   └── screener_service.py      # 核心筛选引擎
│   └── utils/
│       ├── csv_export.py     # CSV 导出工具
│       └── mock_data.py      # Mock 数据种子
├── tests/
├── requirements.txt
├── .env.example
├── run.py                    # 开发服务器入口
└── wsgi.py                   # 生产 WSGI 入口
```

## 快速开始

### 1. 安装依赖

```bash
python -m venv venv
# Windows
venv\Scripts\pip install -r requirements.txt
# macOS/Linux
source venv/bin/activate && pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 API 密钥等配置
```

### 3. 启动服务

```bash
# 开发模式
python run.py
# 默认运行在 http://localhost:5000

# 生产模式 (gunicorn)
gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app
```

首次启动会自动创建数据库并填充 20 只美股的 mock 数据（AAPL, MSFT, GOOGL 等）。

## API 接口

### 健康检查
```
GET /health
```

### 筛选器字段元数据
```
GET /api/screener/fields
```
返回所有可筛选字段（维度A市场技术面 + 维度B绿色碳排），供前端动态渲染表单。

### 执行筛选（核心）
```
POST /api/screener/run
```
请求体：
```json
{
  "filters": {
    "market_cap_basic": {"min": 1000000000, "max": 100000000000},
    "price_earnings_ttm": {"max": 15},
    "turnover": {"min": 5},
    "carbon_intensity_revenue": {"max": 100},
    "carbon_change_yoy": {"max": -5},
    "has_carbon_data": "true"
  },
  "page": 1,
  "pageSize": 50,
  "sortBy": "market_cap_basic",
  "sortOrder": "desc"
}
```

### CSV 导出
```
POST /api/screener/export
```
复用筛选逻辑，返回 CSV 文件下载。

### 预设模板列表
```
GET /api/screener/templates
GET /api/screener/templates/:id
```
四个预设策略：低碳价值陷阱、绿色高成长、净零先锋、高股息绿色标的。

### 个股详情
```
GET /api/stock/:symbol
```
返回公司信息、最新财务指标、碳排放数据及 5 年碳排历史。

### 碳排放趋势
```
GET /api/stock/:symbol/carbon-trend
```
返回 5 年碳排放趋势数据，供前端 recharts 图表展示。

## 数据模型

### companies
| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | VARCHAR(10) PK | 股票代码 |
| name | VARCHAR(200) | 公司名称 |
| sector | VARCHAR(100) | 行业板块 |
| exchange | VARCHAR(50) | 交易所 |

### financial_metrics
| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | FK → companies | 关联公司 |
| date | DATE | 数据日期 |
| close | DECIMAL | 收盘价 |
| pe_ttm | DECIMAL | 市盈率 (TTM) |
| pb | DECIMAL | 市净率 |
| dividend_yield | DECIMAL | 股息率 (%) |
| turnover | DECIMAL | 换手率 (%) |
| market_cap | DECIMAL | 市值 (USD) |
| volume | DECIMAL | 日成交量 |
| week_52_change | DECIMAL | 52周涨跌幅 (%) |
| net_profit_margin | DECIMAL | 净利润率 (%) |
| revenue_growth | DECIMAL | 营收增长率 (%) |

### carbon_emissions
| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | FK → companies | 关联公司 |
| report_year | INT | 报告年份 |
| scope1 | DECIMAL | 直接排放 (tCO2e) |
| scope2 | DECIMAL | 间接排放 (tCO2e) |
| total_emissions | DECIMAL | 总排放量 (Scope1+2) |
| carbon_intensity_revenue | DECIMAL | 碳强度 (tCO2e/$M) |
| carbon_change_yoy | DECIMAL | 碳排同比变化 (%) |

## 数据管道架构

```
[实时行情 API] ──┐
[TradingView]    ├──> [ 数据聚合层 ] ──> [ 统一查询接口 ] ──> [ 前端展示 ]
[碳排数据 API] ──┘     (Redis 缓存)        (/api/screener/run)
   (Bavest)
```

- 行情数据通过 TradingView screener 批量拉取，Redis 缓存 5 分钟
- 碳排放数据单独建表，按年从 Bavest API 同步
- 筛选时后端先匹配财务条件，再与碳排数据做 INNER/LEFT JOIN

## 开发说明

- MVP 阶段使用 mock 数据（20 只 S&P 500 成分股），无需 API 密钥即可完整运行
- 接入真实数据源时，在 `.env` 中配置 `BAVEST_API_KEY` 等密钥
- 数据库迁移：`flask db init && flask db migrate && flask db upgrade`
