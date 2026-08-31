"""多维度评估指标（对照蚂蚁二面「测试方法论」）。

维度：
- 正确性：Pass@k（无偏估计）、编译/解析通过率
- 一致性：同输入多次生成的稳定性（相似度方差）
- 安全性：安全扫描得分（见 server/security.py）
- 效率：token 消耗估计、端到端延迟

参考：HumanEval Pass@k 无偏估计
  pass@k = E[1 - C(n-c, k) / C(n, k)]
其中 n=每题采样数, c=通过样本数, k=取 k 次。
"""
import math
import random
from difflib import SequenceMatcher

HARNESS_VERSION = "1.1.0"  # 评估工具版本（写入审计链，便于回归对比）


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def bootstrap_ci(values, stat_fn=mean, n_boot: int = 1000, alpha: float = 0.05, seed: int = 42):
    """对聚合指标做 bootstrap 置信区间（paired/分层回归对比用）。

    返回 (点估计, 下界, 上界)。values 为样本（0/1 或实数）。
    """
    if not values:
        return (None, None, None)
    rng = random.Random(seed)
    estimates = []
    for _ in range(n_boot):
        sample = [rng.choice(values) for _ in range(len(values))]
        estimates.append(stat_fn(sample))
    estimates.sort()
    lo = estimates[max(0, int((alpha / 2) * n_boot))]
    hi = estimates[min(len(estimates) - 1, int((1 - alpha / 2) * n_boot))]
    return (estimates[len(estimates) // 2], lo, hi)


def pass_at_k_ci(n: int, c: int, k: int, n_boot: int = 2000, seed: int = 7):
    """对 pass@k 估计值做 bootstrap 置信区间（样本级：通过/失败）。"""
    if n == 0:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    per = [1 if i < c else 0 for i in range(n)]
    ests = []
    for _ in range(n_boot):
        s = [rng.choice(per) for _ in range(n)]
        ests.append(pass_at_k(n, sum(s), k))
    ests.sort()
    return (ests[len(ests) // 2], ests[max(0, int(0.025 * n_boot))],
            ests[min(len(ests) - 1, int(0.975 * n_boot))])


def structured_output_validity(results: list[dict]) -> float:
    """结构化输出有效性：非 explain 类 case 的 valid_rate 均值（CI gate 指标）。"""
    html = [r["valid_rate"] for r in results if r["task_type"] != "explain"]
    return mean(html) if html else 1.0


def strata_aggregate(results: list[dict], key: str = "category") -> dict:
    """按分层（category / task_type）聚合 pass@1 / 有效率 / 安全分。"""
    out: dict = {}
    for r in results:
        out.setdefault(r[key], []).append(r)
    return {
        k: {
            "n": len(v),
            "pass@1_mean": round(mean([x["pass@k"]["pass@1"] for x in v]), 3),
            "valid_rate_mean": round(mean([x["valid_rate"] for x in v]), 3),
            "security_mean": round(mean([x["security_score"] for x in v]), 1),
        }
        for k, v in out.items()
    }


def pass_at_k(n: int, c: int, k: int) -> float:
    """Pass@k 无偏估计（HumanEval 论文）。

    n: 每个问题生成的样本总数（n >= k）
    c: 其中通过验收的样本数
    k: 取前 k 次中至少一次通过的概率
    """
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def is_valid_html(code: str, min_len: int = 500) -> bool:
    """验收标准之一：能编译/解析为完整单文件 HTML。

    轻量校验（不引入外部解析库）：
    - 以 <!DOCTYPE 或含 <html 开头（忽略前导空白）
    - 含 </html>
    - 长度达标
    - 非离线模板（不含 '离线模板' 字样，避免把 mock 当真生成）
    """
    if not code or len(code) < min_len:
        return False
    low = code.lower().lstrip()
    if not (low.startswith("<!doctype") or "<html" in low):
        return False
    if "</html>" not in low:
        return False
    if "离线模板" in code:
        return False
    return True


def token_estimate(text: str) -> int:
    """token 消耗估计（中文约 1.5 字/token，英文约 4 字符/token，这里用混合近似）。

    仅用于效率维度的相对比较，非精确计费。
    """
    if not text:
        return 0
    # 中文按字符、其他按 4 字符折算，取较大值以保守估计
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk + other / 4)


def similarity(a: str, b: str) -> float:
    """两段代码相似度 0..1（difflib ratio）。用于一致性度量。"""
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a or "", b or "").ratio()


def consistency(runs: list[str]) -> float:
    """同一输入多次生成的稳定性：所有两两相似度的均值（0..1）。

    1.0 表示每次生成完全一致（最稳定）；越低表示输出越随机。
    """
    if len(runs) < 2:
        return 1.0
    total, pairs = 0.0, 0
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            total += similarity(runs[i], runs[j])
            pairs += 1
    return total / pairs if pairs else 1.0
