# investment-analysis 后端接口

配合 `investment-analysis` 仓库里的 `index.html` 使用。这是一个 Vercel Python
Serverless Function，负责：拉取真实行情/财务数据 → 连同分析要求一起交给 Claude → 
返回 `{"text": "..."}` 给前端。

## 目录结构

```
investment-analysis-backend/
├── api/
│   └── analyze.py       # 核心逻辑：数据拉取 + 调用 Claude
├── requirements.txt      # Python 依赖
├── vercel.json           # Vercel 运行时配置
└── .env.example          # 环境变量示例
```

## 部署步骤

1. **合并到你现有仓库，或单独建一个仓库**
   把 `api/analyze.py`、`requirements.txt`、`vercel.json` 放进
   `investment-analysis` 仓库根目录（和 `index.html` 同级）。

2. **在 Vercel 导入这个仓库**
   - vercel.com → New Project → 选择这个 GitHub 仓库 → Deploy

3. **配置环境变量**（Vercel 项目 → Settings → Environment Variables）
   - `ANTHROPIC_API_KEY`：你的 Claude API Key（必填，绝不要写进前端代码）
   - `ANTHROPIC_MODEL`：可选，默认 `claude-sonnet-4-6`
   - `ALLOWED_ORIGIN`：可选，限制只有你的前端域名能调用这个接口

4. **部署完成后会得到一个地址**，形如：
   `https://investment-analysis-xxxx.vercel.app/api/analyze`

5. **回到 `index.html` 页面**，在左侧「后端接口」输入框里填上这个地址
   （注意要完整路径，含 `/api/analyze`）。填完后左上角状态会从
   "演示模式" 变成 "后端接口模式"。

## 数据源说明

- **A股**：用 `akshare`，走 `stock_individual_info_em` / `stock_financial_abstract`
  / `stock_zh_a_hist` 拉取基本信息、财务摘要、近60个交易日行情。
- **港股 / 美股 / 中概股**：用 `yfinance`，港股会自动把代码补成 `xxxx.HK` 格式。

akshare 的接口偶尔会随数据源改版调整字段名或函数名，如果某天报错，
先去 akshare 官方文档核对对应函数是否还叫这个名字。

## 已知限制 / 后续可以加强的地方

- 目前每次请求都是实时拉数据，没有做缓存，模块多、并发高的时候可能会撞
  数据源的限流。可以加一层 Redis/KV 缓存（Vercel KV）按 ticker 缓存几分钟。
- `akshare` 在 Vercel 的 Python Serverless 环境里体积较大、冷启动会慢一些，
  如果觉得启动慢，可以考虑把数据抓取单独做成一个常驻服务（比如小型云主机
  跑 FastAPI），Vercel 函数只做转发 + 调用 Claude。
- 目前没有做频率限制/鉴权，`ALLOWED_ORIGIN` 只是浏览器端的 CORS 限制，
  不能防止有人直接用 curl 调用你的接口消耗你的 API 额度。如果要公开分享
  这个前端页面，建议加一个简单的接口密钥校验。
