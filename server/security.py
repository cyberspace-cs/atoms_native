"""AI 生成代码的安全审计（SAST 风格，轻量、零依赖）。

对照 **OWASP Top 10 for LLM Applications 2025** + OWASP Web Top 10:2025：

LLM 应用专项（生成式 AI 代码场景）：
- LLM01 提示注入（Prompt Injection）：用户输入/间接注入、分隔符混淆、编码绕过
- LLM02 敏感信息泄露：前端硬编码密钥、PII 直接外发
- LLM05 不当输出处理（Improper Output Handling）：把不可信内容(XSS/SQLi/RCE)直接渲染/执行
- LLM06 过度代理（Excessive Agency）：高危操作用户未确认、最小权限缺失
- LLM07 系统提示泄露（System Prompt Leakage）：把系统提示/密钥写进生成产物

Web 通用（保留原有）：
- A03 注入：innerHTML XSS / eval 代码注入
- A02 硬编码密钥 / A08 供应链（不可信外部资源）
- A05 缺失 CSP / 沙箱逃逸

设计原则：做「生成后自动门禁」级别的低成本模式匹配 + 启发式，能在
Demo→生产过渡期拦住最常见的危险模式。输出可量化安全分(0-100) + 分级 findings。
每个 finding 带 `owasp` 字段便于前端按 LLM Top 10 维度展示。
"""
import base64
import binascii
import re
from typing import Optional

# 允许的可信 CDN（白名单）；其余外部 script/iframe 视为供应链风险
SAFE_CDN = (
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
    "code.jquery.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "esm.sh",
)

# 硬编码密钥 / 凭证 正则（命中即高危）—— 覆盖 LLM02 与 A02
SECRET_PATTERNS = [
    (r"""['"\s](?:api[_-]?key|apikey|secret|token|password|passwd|pwd|private[_-]?key)['"\s]*[:=]\s*['"][A-Za-z0-9_\-]{16,}['"]""",
     "硬编码密钥/密码字面量"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "OpenAI/类 OpenAI 风格 API Key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS Access Key ID"),
    (r"\bghp_[A-Za-z0-9]{36}\b", "GitHub Personal Access Token"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "Google API Key"),
    (r"\bya29\.[0-9A-Za-z_\-]{30,}\b", "Google OAuth Token"),
    (r"\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b", "Bearer Token 明文"),
    (r"\b-----BEGIN[ A-Z]*PRIVATE KEY-----", "PEM 私钥"),
]

# ---- LLM01 提示注入检测（OWASP LLM01）----
# 直接注入：明确要求模型忽略/覆盖指令（中英文）
PROMPT_INJECTION_DIRECT = [
    r"ignore (?:all |the |any |previous )?(?:previous |above |prior )?instructions",
    r"disregard (?:all |the |previous )?instructions",
    r"forget (?:your |all |the )?(?:instructions|rules|guidelines|system prompt)",
    r"you are now",
    r"system prompt",
    r"output the (?:system|hidden) (?:prompt|instructions)",
    r"reveal your (?:prompt|instructions|system message)",
    # 中文
    r"忽略.{0,6}(之前|先前|所有|以上|上述).{0,4}(指令|指示|规则|设定|prompt)",
    r"忘记.{0,6}(你的|所有|之前|上面).{0,4}(指令|规则|设定|prompt)",
    r"你(现在|此刻|立刻).{0,4}(是|变成|扮演|作为)",
    r"输出.{0,4}(你的|系统|隐藏).{0,4}(提示|指令|prompt)",
    r"透露.{0,4}(你的)?(提示|指令|系统设定|prompt)",
]
# 间接注入：诱导模型执行非预期动作（角色扮演越狱、DAN 等，中英文）
PROMPT_INJECTION_INDIRECT = [
    r"\bjailbreak\b",
    r"\bDAN\b",
    r"developer mode",
    r"no restrictions",
    r"without (?:any )?(?:filter|limit|moral|censorship)",
    r"pretend to be",
    r"roleplay as",
    r"act as (?:if|though)",
    # 中文
    r"越狱",
    r"角色扮演",
    r"假装(成|为|是)",
    r"开发者模式",
    r"无限制",
    r"不受(任何)?(限制|审查|约束)",
]
# 高风险意图：要求外发数据 / 执行命令 / 提权（中英文）
PROMPT_INJECTION_RISKY_INTENT = [
    r"send .*(?:to|via)?\s*(?:http|email|webhook|telegram|discord)",
    r"exfiltrat",
    r"run (?:a )?command",
    r"execute (?:the )?(?:following )?code",
    r"delete (?:all|every|the) (?:file|database|record)",
    r"bypass (?:the )?(?:auth|login|security|paywall)",
    # 中文
    r"发送.{0,8}(到|外部|邮箱|webhook)",
    r"外发",
    r"执行(命令|代码)",
    r"删除.{0,6}(所有|全部|文件|数据库|记录)",
    r"绕过.{0,6}(登录|验证|安全|鉴权)",
]

# ---- LLM07 系统提示泄露：生成产物里把系统提示/密钥暴露给用户 ----
SYSTEM_PROMPT_LEAK_PATTERNS = [
    r"system prompt",
    r"system message",
    r"you are (?:a|an) (?:language model|AI|assistant)",
    r"your (?:hidden|secret) instructions",
]

_SEVERITY = {"high": 3, "medium": 2, "low": 1, "info": 0}


def _severity_rank(sev: str) -> int:
    return _SEVERITY.get(sev, 1)


def _looks_encoded(s: str) -> Optional[str]:
    """检测疑似 base64/hex 编码的注入载荷（分隔符混淆绕过）。"""
    s = s.strip()
    if len(s) < 16:
        return None
    # base64 形态
    if re.fullmatch(r"[A-Za-z0-9+/=]{16,}", s):
        try:
            dec = base64.b64decode(s + "=" * (-len(s) % 4), validate=True).decode("utf-8", "ignore")
            if re.search(r"(ignore|instruction|system|jailbreak|you are)", dec, re.I):
                return dec
        except (binascii.Error, ValueError):
            pass
    # hex 形态
    if re.fullmatch(r"(?:0x)?[0-9a-fA-F]{32,}", s):
        try:
            dec = bytes.fromhex(s[2:] if s.startswith("0x") else s).decode("utf-8", "ignore")
            if re.search(r"(ignore|instruction|system|jailbreak)", dec, re.I):
                return dec
        except ValueError:
            pass
    return None


def scan_idea(idea: str) -> dict:
    """扫描用户输入的 idea，检测提示注入（OWASP LLM01）。

    返回结构化结果：
      injection: bool
      risk: low|medium|high（高风险意图 / 编码绕过 => high）
      categories: 命中的 LLM01 子类别（direct/indirect/risky_intent/encoded）
      hints: 命中的原始模式或解码后的可疑串
    pipeline 应根据结果决定是否对 idea 做「分隔符隔离」(delimiter separation)。
    """
    if not idea:
        return {"injection": False, "risk": "low", "categories": [], "hints": []}
    low = idea.lower()
    cats: list[str] = []
    hints: list[str] = []
    risk = "low"

    # 直接注入
    for pat in PROMPT_INJECTION_DIRECT:
        if re.search(pat, low):
            cats.append("direct")
            hints.append(pat)
    # 间接注入 / 越狱
    for pat in PROMPT_INJECTION_INDIRECT:
        if re.search(pat, low):
            cats.append("indirect")
            hints.append(pat)
    # 高风险意图
    for pat in PROMPT_INJECTION_RISKY_INTENT:
        if re.search(pat, low):
            cats.append("risky_intent")
            hints.append(pat)
    # 编码绕过（base64/hex）
    for tok in re.split(r"[\s,;]+", idea):
        dec = _looks_encoded(tok)
        if dec:
            cats.append("encoded")
            hints.append("encoded:" + dec[:60])

    if cats:
        if "risky_intent" in cats or "encoded" in cats:
            risk = "high"
        elif "direct" in cats or "indirect" in cats:
            risk = "medium"
    return {"injection": bool(cats), "risk": risk, "categories": sorted(set(cats)), "hints": hints}


def scan_html(code: str) -> dict:
    """扫描生成的单文件应用，返回 {score, findings:[...]}。

    findings 元素: {severity, category, owasp, line, snippet, advice}
      owasp: 命中的 OWASP LLM Top 10 编号（如 "LLM05"）或 Web Top 10（如 "A03"）
    score: 100 减去加权扣分（high=-25, medium=-10, low=-4），下限 0。
    """
    if not code:
        return {"score": 0, "findings": [{
            "severity": "high", "category": "empty", "owasp": "A03",
            "line": 0, "snippet": "(空输出)", "advice": "生成内容为空，无法评估。"}]}
    findings: list[dict] = []
    lines = code.splitlines()

    def add(sev, cat, owasp, idx, snippet, advice):
        findings.append({
            "severity": sev, "category": cat, "owasp": owasp,
            "line": idx + 1 if idx >= 0 else 0,
            "snippet": (snippet or "")[:160], "advice": advice,
        })

    # 1) XSS / 不当输出处理（A03 / LLM05）：innerHTML 等接变量
    xss_re = re.compile(
        r"(innerHTML|outerHTML|insertAdjacentHTML|document\.write)\s*(?:\(|\+= |=)")
    for i, ln in enumerate(lines):
        if xss_re.search(ln):
            arg = ln[xss_re.search(ln).end():]
            is_literal = bool(re.match(r"\s*['\"`]", arg.strip())) and ("+" not in arg[:40])
            add("high" if not is_literal else "medium", "XSS (CWE-79)", "LLM05",
                i, ln.strip(),
                "避免把用户/外部输入直接写入 HTML。用 textContent / createTextNode，"
                "或对内容做 escape 后再插入（不当输出处理，OWASP LLM05）。")

    # 2) 代码注入（A03 / LLM05）：eval / new Function / 字符串 setTimeout
    for i, ln in enumerate(lines):
        if re.search(r"\beval\s*\(", ln) or re.search(r"new\s+Function\s*\(", ln):
            add("high", "Code Injection (CWE-95)", "LLM05", i, ln.strip(),
                "禁止对不可信输入使用 eval / new Function。改用安全的解析/数据结构。")
        if re.search(r"setTimeout\s*\(\s*['\"]", ln) or re.search(r"setInterval\s*\(\s*['\"]", ln):
            add("medium", "Code Injection (CWE-95)", "LLM05", i, ln.strip(),
                "setTimeout/setInterval 的字符串参数会被当作代码执行，改用函数引用。")

    # 3) SQL 注入（A03 / LLM05）：字符串拼接构建 SQL 并执行
    sql_inject_re = re.compile(
        r"(executeSql|execSQL|\.query|\.run|db\.exec)\s*\([^)]*\+", re.I)
    for i, ln in enumerate(lines):
        if sql_inject_re.search(ln) or re.search(r"(SELECT|INSERT|UPDATE|DELETE)[^\n;]*\+", ln, re.I):
            add("high", "SQL Injection (CWE-89)", "LLM05", i, ln.strip(),
                "禁止字符串拼接 SQL。使用参数化查询（prepared statement）。")

    # 4) 硬编码密钥 / PII 外发（A02 / LLM02）
    for pat, label in SECRET_PATTERNS:
        for i, ln in enumerate(lines):
            if re.search(pat, ln):
                add("high", "Hardcoded Secret (CWE-798)", "LLM02", i, ln.strip(),
                    "不要将密钥写入前端代码（浏览器侧任何人可见）。应放后端/环境变量，"
                    "前端通过自身后端代理调用（敏感信息泄露，OWASP LLM02）。")
    # 疑似把数据外发到不可信地址（LLM02：敏感信息泄露）
    for i, ln in enumerate(lines):
        for m in re.finditer(r"(fetch|XMLHttpRequest|navigator\.sendBeacon)\s*\([^)]*['\"](https?://[^'\"]+)['\"]", ln):
            host = re.sub(r"^https?://", "", m.group(2)).split("/")[0]
            if not any(host == c or host.endswith("." + c) for c in SAFE_CDN):
                add("medium", "Sensitive Data Egress (CWE-200)", "LLM02", i, m.group(2),
                    "检测到向非白名单外部地址发送数据，存在敏感信息泄露风险（OWASP LLM02）。"
                    "确认目标为可信端点且不含用户隐私。")

    # 5) 过度代理（LLM06）：高危操作缺少用户确认 / 最小权限
    for i, ln in enumerate(lines):
        if re.search(r"window\.location\s*=\s*['\"]https?://", ln) or re.search(r"window\.open\s*\(\s*['\"]https?://", ln):
            add("medium", "Unbounded Redirect (CWE-601)", "LLM06", i, ln.strip(),
                "导航/跳转至外部地址应经用户显式确认（过度代理，OWASP LLM06）。")
        if re.search(r"form[^>]*action\s*=\s*['\"]https?://", ln, re.I) and "method" in ln.lower() and "post" in ln.lower():
            add("medium", "Unbounded Form Submit (CWE-601)", "LLM06", i, ln.strip(),
                "表单向外部地址 POST 应确认目标可信（过度代理，OWASP LLM06）。")
        if re.search(r"localStorage\.(?:setItem|getItem)\s*\([^)]*(?:token|secret|password|key|pwd)", ln, re.I):
            add("medium", "Secret in Client Storage (CWE-522)", "LLM06", i, ln.strip(),
                "不要把凭证/密钥存进 localStorage（前端可读，过度暴露，OWASP LLM06）。")

    # 6) 系统提示泄露（LLM07）：把系统提示/角色说明写进面向用户的产物
    for i, ln in enumerate(lines):
        for pat in SYSTEM_PROMPT_LEAK_PATTERNS:
            if re.search(pat, ln, re.I):
                add("low", "System Prompt Leakage (CWE-200)", "LLM07", i, ln.strip(),
                    "避免在生成产物（尤其用户可见文案）中泄露系统提示/模型身份（OWASP LLM07）。")
                break

    # 7) 外部 script / iframe 引入（供应链，A08）
    for i, ln in enumerate(lines):
        for m in re.finditer(r"<(script|iframe)[^>]*\ssrc\s*=\s*['\"]([^'\"]+)['\"]", ln, re.I):
            src = m.group(2)
            if src.startswith(("http://", "https://")):
                host = re.sub(r"^https?://", "", src).split("/")[0]
                if not any(host == c or host.endswith("." + c) for c in SAFE_CDN):
                    add("medium", "Untrusted External Source (CWE-829)", "A08", i, src,
                        f"引入非白名单外部资源（{host}），存在供应链/数据外泄风险。"
                        "改用白名单 CDN 或将资源本地化。")
            elif not src.startswith(("data:", "blob:", "/", "./", "../")):
                add("medium", "Untrusted External Source (CWE-829)", "A08", i, src,
                    "不可信来源的资源引入。")

    # 8) 缺失 CSP / sandbox（信息级，A05）
    if not re.search(r"Content-Security-Policy", code, re.I):
        add("low", "Missing CSP (CWE-1021)", "A05", -1, "(文档级)",
            "建议添加 <meta http-equiv=\"Content-Security-Policy\"> 降低 XSS/注入影响面。")

    # 9) 危险全局属性（allow-same-origin + allow-scripts 沙箱逃逸）
    for i, ln in enumerate(lines):
        if re.search(r"sandbox\s*=", ln, re.I) and "allow-scripts" in ln and "allow-same-origin" in ln:
            add("medium", "Sandbox Escape (CWE-250)", "A05", i, ln.strip(),
                "sandbox 同时包含 allow-scripts 与 allow-same-origin 等于未沙箱，移除其一。")

    # 计算安全分（同一行多命中不无限叠加：按行去重计数）
    weights = {"high": 25, "medium": 10, "low": 4, "info": 0}
    penalty = sum(weights[f["severity"]] for f in findings)
    score = max(0, 100 - penalty)

    findings.sort(key=lambda f: -_severity_rank(f["severity"]))
    return {"score": score, "findings": findings}


def scan_pipeline_input(idea: str) -> dict:
    """pipeline 入口：对用户输入做 LLM01 扫描，并给出「分隔符隔离」建议。

    当检测到注入风险时，返回 isolate=True，pipeline 应用三引号/XML 标签把
    idea 包成不可执行的上下文（OWASP LLM01 缓解：delimiter separation）。
    """
    res = scan_idea(idea)
    return {
        **res,
        "isolate": res["injection"] and res["risk"] in ("medium", "high"),
    }


def summarize(scan_result: dict) -> str:
    """给人看的简短结论。"""
    score = scan_result.get("score", 0)
    n = len(scan_result.get("findings", []))
    highs = [f for f in scan_result.get("findings", []) if f["severity"] == "high"]
    if score >= 90:
        return f"安全分 {score}/100：未发现高危问题（{n} 项提示）。"
    if highs:
        cats = sorted({f.get("owasp", "") for f in highs if f.get("owasp")})
        return f"安全分 {score}/100：发现 {len(highs)} 项高危问题（{', '.join(cats)}），建议修复后再交付。"
    return f"安全分 {score}/100：发现 {n} 项中低风险，建议酌情处理。"
