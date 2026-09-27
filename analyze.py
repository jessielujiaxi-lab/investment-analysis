"""
Vercel Python Serverless Function
接收前端 { ticker, market, module, moduleLabel, prompt }
1. 根据 market 拉取真实行情/财务数据（A股用 akshare，港股/美股/中概股用 yfinance）
2. 把真实数据 + 模块 prompt 一起交给 Claude 生成分析文本
3. 返回 { "text": "..." }

部署位置：<repo>/api/analyze.py
Vercel 会自动把它映射为 POST /api/analyze
"""

import os
import json
import traceback
from http.server import BaseHTTPRequestHandler

import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# 允许调用这个接口的前端来源（GitHub Pages / Vercel 静态页地址），
# 部署时改成你自己的域名，多个用逗号分隔
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGIN", "*")


# ---------------------------------------------------------------------------
# 数据获取部分
# ---------------------------------------------------------------------------

def fetch_data_a_share(ticker: str) -> dict:
    """A股数据：用 akshare。ticker 支持 '600519' 或 '贵州茅台' 这种输入，
    这里假设前端已经传入 6 位数字代码；如果传的是名称，先做一次简单映射。"""
    import akshare as ak

    code = ticker.strip()
    data = {"source": "akshare", "ticker": code}

    # 如果用户输入的是中文名称而不是代码，尝试用 A 股列表反查代码
    if not code.isdigit():
        try:
            spot = ak.stock_zh_a_spot_em()
            row = spot[spot["名称"].str.contains(code, na=False)]
            if not row.empty:
                code = row.iloc[0]["代码"]
                data["ticker"] = code
                data["resolved_name"] = row.iloc[0]["名称"]
        except Exception as e:
            data["name_resolution_error"] = str(e)

    # 基本信息
    try:
        info = ak.stock_individual_info_em(symbol=code)
        data["basic_info"] = dict(zip(info["item"], info["value"]))
    except Exception as e:
        data["basic_info_error"] = str(e)

    # 财务摘要（营收、净利润、ROE 等核心指标，近几期）
    try:
        fin = ak.stock_financial_abstract(symbol=code)
        data["financial_abstract"] = fin.tail(8).to_dict(orient="records")
    except Exception as e:
        data["financial_abstract_error"] = str(e)

    # 近一年行情（用于判断趋势、波动率，不是给模型编故事用的）
    try:
        hist = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        data["price_history_tail"] = hist.tail(60).to_dict(orient="records")
    except Exception as e:
        data["price_history_error"] = str(e)

    return data


def fetch_data_yfinance(ticker: str, market: str) -> dict:
    """港股 / 美股 / 中概股：用 yfinance。
    港股需要类似 '0700.HK' 的格式，美股/中概股直接用代码，如 'AAPL'。"""
    import yfinance as yf

    symbol = ticker.strip()
    if market == "港股" and not symbol.upper().endswith(".HK"):
        # 用户输入 700 或 0700 都补齐成 4 位 + .HK
        digits = "".join(ch for ch in symbol if ch.isdigit())
        if digits:
            symbol = f"{digits.zfill(4)}.HK"

    data = {"source": "yfinance", "ticker": symbol}
    tk = yf.Ticker(symbol)

    try:
        data["basic_info"] = {
            k: v for k, v in (tk.info or {}).items()
            if isinstance(v, (str, int, float)) and k in (
                "longName", "sector", "industry", "marketCap", "trailingPE",
                "forwardPE", "priceToBook", "returnOnEquity", "profitMargins",
                "revenueGrowth", "totalRevenue", "grossMargins", "currency",
                "fullTimeEmployees",
            )
        }
    except Exception as e:
        data["basic_info_error"] = str(e)

    try:
        fin = tk.financials
        data["financials_tail"] = fin.iloc[:, :4].to_dict() if fin is not None else {}
    except Exception as e:
        data["financials_error"] = str(e)

    try:
        hist = tk.history(period="1y")
        data["price_history_tail"] = hist.tail(60).reset_index().to_dict(orient="records")
    except Exception as e:
        data["price_history_error"] = str(e)

    return data


def fetch_market_data(ticker: str, market: str) -> dict:
    try:
        if market == "A股":
            return fetch_data_a_share(ticker)
        else:
            return fetch_data_yfinance(ticker, market)
    except Exception as e:
        # 数据源失败不应该让整个请求 500，而是把情况如实告诉模型，
        # 模型据此在分析里明确说明"数据缺失"，而不是编造数字
        return {
            "source": "none",
            "ticker": ticker,
            "fetch_error": str(e),
            "trace": traceback.format_exc(limit=3),
        }


# ---------------------------------------------------------------------------
# Claude 调用部分
# ---------------------------------------------------------------------------

def build_system_prompt(ticker: str, market: str, module_label: str, market_data: dict) -> str:
    return f"""你是一名机构级投资分析师，正在为「{ticker}」（{market}）撰写「{module_label}」模块的分析。

以下是通过数据接口拉取到的真实数据（JSON），这是你唯一可信的数据来源：
{json.dumps(market_data, ensure_ascii=False, default=str)[:12000]}

硬性要求：
1. 只使用上面提供的数据进行事实性陈述；如果某项数据缺失或抓取失败（字段名包含 error），
   必须在分析中明确指出"该项数据暂缺"，不得编造具体数字。
2. 明确区分"数据事实"与"你的推断/判断"，例如用"数据显示……"和"据此推断……"分开表述。
3. 输出使用 Markdown 小标题（### 开头）分段，语言简洁、可执行，避免空话套话。
4. 不构成投资建议，最后一行注明："以上内容仅为基于公开数据的辅助研究，不构成投资建议。"
"""


def call_claude(system_prompt: str, user_prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("服务端未配置 ANTHROPIC_API_KEY 环境变量。")

    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 2000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = data.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


# ---------------------------------------------------------------------------
# HTTP 入口（Vercel Python runtime 约定的 handler 类）
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGINS)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")

            ticker = str(body.get("ticker", "")).strip()
            market = str(body.get("market", "A股")).strip()
            module_label = str(body.get("moduleLabel", body.get("module", "")))
            user_prompt = str(body.get("prompt", ""))

            if not ticker:
                raise ValueError("缺少 ticker 参数。")

            market_data = fetch_market_data(ticker, market)
            system_prompt = build_system_prompt(ticker, market, module_label, market_data)
            text = call_claude(system_prompt, user_prompt)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": {"message": str(e)}
            }, ensure_ascii=False).encode("utf-8"))
