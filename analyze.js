const OPENAI_API_URL = "https://api.openai.com/v1/responses";
const MODEL = process.env.OPENAI_MODEL || "gpt-5.6-luna";

const MODULE_PROMPTS = {
  business: "分析商业模式、收入结构、单位经济模型、客户价值、成本结构、护城河与规模效应。区分已知事实、合理推断和待验证信息。",
  industry: "分析行业规模、增长、竞争格局、主要竞争对手、市场份额、进入壁垒、替代威胁和 Porter 五力。",
  mgmt: "分析管理层背景、资本配置、股权结构、治理、激励机制、企业文化和历史决策。只陈述可验证事实；无法验证的内容明确标注。",
  financial: "分析营收、毛利率、营业利润率、净利润、ROE、自由现金流、资本开支、资产负债表、现金及债务，并指出异常项。",
  growth: "分析未来增长驱动、市场空间、渗透率、价格/销量、产品周期、海外扩张和增长的可持续性，并给出情景区间。",
  dcf: "建立保守、基准、乐观三个 DCF 情景。明确收入增长、利润率、税率、折旧、资本开支、营运资本、WACC 和终值增长率等假设，并说明数据缺口。",
  risk: "识别最重要的经营、财务、估值、竞争、监管和治理风险；给出触发条件、潜在影响和需要跟踪的指标。",
  conclusion: "综合前述信息，给出研究结论、核心逻辑、关键假设、需要跟踪的指标和在什么条件下结论会失效。不要伪装成确定性的投资建议。",
  bear: "构建最强空头逻辑：哪些关键假设可能错误、竞争如何恶化、增长如何下修、估值如何压缩，并给出可证伪指标。",
  holes: "审查多头逻辑中的漏洞、认知偏误、幸存者偏差、叙事偏差、数据选择偏差和未经验证的关键假设。"
};

function sendJson(res, status, body) {
  res.status(status).setHeader("Content-Type", "application/json; charset=utf-8");
  return res.end(JSON.stringify(body));
}

function getOutputText(data) {
  if (typeof data?.output_text === "string") return data.output_text;

  const parts = [];

  for (const item of data?.output || []) {
    for (const c of item?.content || []) {
      if (typeof c?.text === "string") {
        parts.push(c.text);
      }
    }
  }

  return parts.join("\n").trim();
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return sendJson(res, 405, {
      error: "Method Not Allowed"
    });
  }

  if (!process.env.OPENAI_API_KEY) {
    return sendJson(res, 500, {
      error: "服务器未配置 OPENAI_API_KEY。"
    });
  }

  let body = req.body || {};

  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch {
      return sendJson(res, 400, {
        error: "请求体不是有效 JSON。"
      });
    }
  }

  const ticker = String(body.ticker || "").trim();
  const market = String(body.market || "").trim();
  const module = String(body.module || "").trim();
  const moduleLabel = String(body.moduleLabel || module).trim();
  const prompt = String(body.prompt || "").trim();

  if (!ticker || !module) {
    return sendJson(res, 400, {
      error: "缺少 ticker 或 module。"
    });
  }

  const moduleInstruction =
    MODULE_PROMPTS[module] ||
    prompt ||
    "对该投资标的进行结构化研究，并明确区分事实、推断和未知信息。";

  const systemPrompt = [
    "你是一个严谨的机构级股票研究助手。",
    "目标是帮助用户进行研究，而不是替用户做投资决定。",
    "不得编造实时价格、财务数据、市场份额、管理层事实或新闻。",
    "当前接口没有自动提供实时行情或财务数据库，因此没有数据就明确写“数据缺失/待验证”，不要猜。",
    "把事实、推断、假设和不确定性明确区分。",
    "如果涉及估值，所有关键假设必须显式列出。",
    "输出使用中文，结构清晰，适合直接展示在投研网页中。",
    "不要输出 JSON；直接输出 Markdown/纯文本报告。",
    "不要使用绝对化的买卖保证或收益保证。"
  ].join("\n");

  const userPrompt = [
    `标的：${ticker}`,
    `市场：${market || "未指定"}`,
    `分析模块：${moduleLabel}`,
    "",
    "模块研究要求：",
    moduleInstruction,
    "",
    "用户原始要求：",
    prompt || "无",
    "",
    "请输出该模块的完整研究报告。"
  ].join("\n");

  try {
    const upstream = await fetch(OPENAI_API_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENAI_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: MODEL,
        instructions: systemPrompt,
        input: userPrompt
      })
    });

    const raw = await upstream.text();

    let data;

    try {
      data = JSON.parse(raw);
    } catch {
      data = null;
    }

    if (!upstream.ok) {
      const message =
        data?.error?.message ||
        `OpenAI API 返回 HTTP ${upstream.status}`;

      return sendJson(
        res,
        upstream.status >= 500 ? 502 : upstream.status,
        {
          error: message
        }
      );
    }

    const text = getOutputText(data);

    if (!text) {
      return sendJson(res, 502, {
        error: "OpenAI 没有返回可显示的文本。"
      });
    }

    return sendJson(res, 200, {
      text,
      model: MODEL,
      module
    });

  } catch (error) {
    return sendJson(res, 502, {
      error: `调用 OpenAI API 失败：${error?.message || "Unknown error"}`
    });
  }
}
