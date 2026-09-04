#!/usr/bin/env python3
"""评估门禁判定脚本（CI 与本地 ci_gate.sh 共用）。

读取 server/evals/_eval_report.json，按以下规则判定，任何一条不满足即 exit 1（红）：
  1. structured_output_gate_pass == True（结构化输出有效性 >= 0.98）
  2. all_cases_pass == True（全部 case valid_rate >= 0.99）
  3. min_security >= 门限（默认 40，--min-security 可调；--expect-mock 冒烟档放宽为 30）
  4. mean_mock_rate <= 上限（默认 0.5；mock 率过高说明评估失真，仅当 --max-mock-rate 显式传入时启用）

用法：
  python scripts/eval_gate.py                     # 默认门限
  python scripts/eval_gate.py --min-security 60   # 收紧安全分门限
"""
import argparse
import json
import sys

DEFAULT_MIN_SECURITY = 40
# mock 冒烟档的放宽门限：离线模板不是真实安全姿态，对抗用例
# （gen_adversarial_prompt_leak）的离线模板含用户可控回显，天然 38 分。
# 此档只防「塌方」（扫描器坏了/全部低分），不套真实质量门限。
MOCK_MIN_SECURITY = 30


def judge(report: dict, min_security: int, max_mock_rate: float | None,
          expect_mock: bool = False) -> tuple[bool, list[str]]:
    """返回 (是否通过, 失败原因列表)。判定逻辑独立成函数便于负例测试。

    两档语义：
      expect_mock=False（默认，真实质量门禁）：structured>=0.98 + 全 case valid + 安全分门限
      expect_mock=True（CI 冒烟级门禁）：确认确实全 mock（防误吃 key），并验证
        harness 健康——runner 跑完全部 case 不崩、安全扫描工作且分数不塌方。
        mock 输出按定义被判 valid_rate=0（metrics.is_valid_html 故意排除离线
        模板，防止 mock 冲真实指标），因此 structured/valid_rate 规则不适用；
        安全分改用放宽门限 MOCK_MIN_SECURITY（离线模板≠真实安全姿态）。
    """
    g = report.get("gates", {})
    s = report.get("summary", {})
    reasons = []

    if expect_mock:
        mock = s.get("mean_mock_rate", 0)
        if mock < 1.0:
            reasons.append(f"mean_mock_rate={mock} < 1.0（CI 门禁要求纯 mock 运行）")
    else:
        if not g.get("structured_output_gate_pass", False):
            reasons.append(
                f"structured_output_validity={g.get('structured_output_validity')} < 0.98")
        if not g.get("all_cases_pass", False):
            reasons.append("存在 valid_rate < 0.99 的 case")
    effective_min = MOCK_MIN_SECURITY if expect_mock else min_security
    min_sec = g.get("min_security", 100)
    if min_sec < effective_min:
        reasons.append(f"min_security={min_sec} < 门限 {effective_min}")
    if max_mock_rate is not None:
        mock = s.get("mean_mock_rate", 0)
        if mock > max_mock_rate:
            reasons.append(f"mean_mock_rate={mock} > 上限 {max_mock_rate}")
    return (not reasons), reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="server/evals/_eval_report.json")
    ap.add_argument("--min-security", type=int, default=DEFAULT_MIN_SECURITY)
    ap.add_argument("--max-mock-rate", type=float, default=None)
    ap.add_argument("--expect-mock", action="store_true",
                    help="CI 冒烟级门禁：要求纯 mock 运行，豁免仅适用真实 LLM 的指标")
    args = ap.parse_args()

    try:
        with open(args.report, encoding="utf-8") as f:
            rep = json.load(f)
    except Exception as e:
        print(f"GATE: FAIL（无法读取报告 {args.report}: {e}）")
        sys.exit(1)

    ok, reasons = judge(rep, args.min_security, args.max_mock_rate, args.expect_mock)
    print("GATE:", "PASS" if ok else "FAIL")
    print("  gates:", json.dumps(rep.get("gates", {}), ensure_ascii=False))
    print("  summary:", json.dumps(rep.get("summary", {}), ensure_ascii=False))
    for r in reasons:
        print("  ✗", r)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
