#!/usr/bin/env python3
"""评估门禁自身的测试：证明门禁「回归失败则红、正常则绿」。

这是 #23 CI/CD 评估门禁的关键一环——门禁脚本如果本身失效（永远绿），
整个质量防线就是纸面功夫。本测试用注入的合成 report 验证：

  负例（必须 FAIL / exit 1）：
    - 结构化输出有效性不达标
    - 存在 valid_rate 不达标的 case
    - 安全分低于门限
    - mock 率超上限
    - 报告文件缺失/损坏
  正例（必须 PASS / exit 0）：
    - 各项达标的正常报告

用法：python tests/gate_test.py   （纯 stdlib，无第三方依赖）
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(ROOT, "scripts", "eval_gate.py")
FAILS = []


def good_report(**overrides):
    """构造一份各项达标的正常报告，overrides 可篡改任意字段制造负例。"""
    gates = {
        "structured_output_validity": 1.0,
        "structured_output_gate_pass": True,
        "all_cases_pass": True,
        "min_security": 85,
        **overrides.get("gates", {}),
    }
    summary = {"mean_mock_rate": 0.0, **overrides.get("summary", {})}
    return {"gates": gates, "summary": summary}


def run_gate(report: dict | None, extra_args: list[str] | None = None) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        if report is not None:
            json.dump(report, f, ensure_ascii=False)
        path = f.name
    try:
        cmd = [sys.executable, GATE, "--report", path] + (extra_args or [])
        return subprocess.run(cmd, capture_output=True).returncode
    finally:
        os.unlink(path)


def check(name: str, expect_fail: bool, report: dict | None,
          extra_args: list[str] | None = None):
    rc = run_gate(report, extra_args)
    ok = (rc != 0) if expect_fail else (rc == 0)
    print(f"{'✅' if ok else '❌'} {name}: exit={rc}（期望{'红 FAIL' if expect_fail else '绿 PASS'}）")
    if not ok:
        FAILS.append(name)


def mock_report(**overrides):
    """mock 运行的报告（metrics.is_valid_html 故意把离线模板判 invalid，
    所以 mock 下 valid_rate/structured 恒 0，CI 冒烟门禁不适用这两条）。"""
    gates = {"structured_output_validity": 0.0,
             "structured_output_gate_pass": False,
             "all_cases_pass": False,
             "min_security": overrides.pop("min_security", 46)}
    return good_report(
        gates=gates,
        summary={"mean_mock_rate": 1.0, **overrides.get("summary", {})},
        **{k: v for k, v in overrides.items() if k != "summary"},
    )


# ---- 负例：回归必须变红 ----
check("结构化输出有效性不达标", True,
      good_report(gates={"structured_output_validity": 0.9,
                         "structured_output_gate_pass": False}))
check("存在不达标 case", True,
      good_report(gates={"all_cases_pass": False}))
check("安全分低于默认门限", True,
      good_report(gates={"min_security": 10}))
check("安全分低于自定义门限", True,
      good_report(gates={"min_security": 70}), ["--min-security", "80"])
check("mock 率超上限", True,
      good_report(summary={"mean_mock_rate": 0.8}), ["--max-mock-rate", "0.5"])
check("报告文件为空损坏", True, None)

# ---- 正例：正常报告必须绿 ----
check("各项达标正常报告", False, good_report())

# ---- CI 冒烟级门禁（--expect-mock）----
check("[mock] 纯 mock + 安全分达标", False, mock_report(), ["--expect-mock"])
check("[mock] 安全分塌方仍要红", True,
      mock_report(min_security=10), ["--expect-mock"])
check("[mock] mock 率不足 1（疑似误吃 key）要红", True,
      mock_report(summary={"mean_mock_rate": 0.5}), ["--expect-mock"])
check("[mock] 报告损坏要红", True, None, ["--expect-mock"])

print()
if FAILS:
    print(f"❌ {len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("✅ 门禁自身测试全部通过——回归确实会红，正常确实会绿。")
