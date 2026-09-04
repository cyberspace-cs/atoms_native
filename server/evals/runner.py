"""评估集运行器：把 cases.json 跑过 pipeline，输出多维度指标报告。

用法：
  python server/evals/runner.py                 # 默认用 deepseek 直连，每 case 跑 1 次
  python server/evals/runner.py --runs 2 --model deepseek
  python server/evals/runner.py --only gen_pure_bmi

指标（对照蚂蚁二面「测试方法论」）：
  - 正确性：pass@k 无偏估计、编译/解析通过率(is_valid_html)
  - 一致性：同输入多次生成相似度（metrics.consistency）
  - 安全性：security.scan_html 得分均值
  - 效率：端到端延迟、token 消耗估计
报告写入 server/evals/_eval_report.json，并打印摘要。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # server/
sys.path.insert(0, HERE)

from metrics import (
    pass_at_k, is_valid_html, token_estimate, consistency, HARNESS_VERSION,
    bootstrap_ci, pass_at_k_ci, structured_output_validity, strata_aggregate, mean,
)
import security
from agent import pipeline
from agent.llm import chat, provider_available, LLM_PROVIDER


def run_generate(case: dict, model: str):
    t0 = time.time()
    gen = pipeline.run_pipeline(case["idea"], model=model)
    final = {}
    while True:
        try:
            next(gen)
        except StopIteration as e:
            final = e.value or {}
            break
    dt = time.time() - t0
    return final.get("code", ""), bool(final.get("mock")), dt


def run_refine_fix(case: dict, model: str, is_fix: bool):
    t0 = time.time()
    gen = pipeline.run_pipeline(
        case["idea"], model=model,
        refine_code=case.get("base_code", ""), refine_msg=case.get("message", ""),
    )
    final = {}
    while True:
        try:
            next(gen)
        except StopIteration as e:
            final = e.value or {}
            break
    dt = time.time() - t0
    # Retaining the previous valid HTML is recovery, not a successful edit.
    # Mock runs retain fixtures only for the separate offline harness gate.
    delivered = final.get("status") == "success" or final.get("mock")
    return final.get("code", "") if delivered else "", bool(final.get("mock")), dt


def run_explain(case: dict, model: str):
    t0 = time.time()
    text, err = chat(model, [
        {"role": "system", "content": "你是代码讲解助手，用中文通俗解释，要点清晰。"},
        {"role": "user", "content": case.get("explain_prompt", case["idea"])},
    ], max_tokens=1200)
    dt = time.time() - t0
    return (text or ""), bool(err), dt


def eval_case(case: dict, model: str, runs: int):
    codes, mocks, times = [], [], []
    for _ in range(runs):
        if case["task_type"] == "explain":
            code, mock, dt = run_explain(case, model)
        elif case["task_type"] in ("refine", "fix"):
            code, mock, dt = run_refine_fix(case, model, case["task_type"] == "fix")
        else:
            code, mock, dt = run_generate(case, model)
        codes.append(code)
        mocks.append(mock)
        times.append(dt)

    # 正确性
    valid = [is_valid_html(c, case["accept"].get("min_len", 500)) for c in codes]
    n = len(codes)
    c_valid = sum(valid)
    # explain 类用「非空 + 命中 must_contain」代替 HTML 校验
    if case["task_type"] == "explain":
        must = case["accept"].get("must_contain", [])
        valid = [ (bool(c) and all(m in c for m in must)) for c in codes ]
        c_valid = sum(valid)
    pass1 = pass_at_k(n, c_valid, 1)
    passk = {f"pass@{k}": pass_at_k(n, c_valid, k) for k in (2, 3) if k <= n}
    passk["pass@1"] = pass1
    # pass@1 的 bootstrap 置信区间（样本级）
    p1_pt, p1_lo, p1_hi = pass_at_k_ci(n, c_valid, 1)

    # 安全性（仅对 HTML 类）
    if case["task_type"] != "explain":
        sec_scores = [security.scan_html(c)["score"] for c in codes]
        sec_mean = sum(sec_scores) / len(sec_scores) if sec_scores else 100
        sec_findings = sum(len(security.scan_html(c)["findings"]) for c in codes)
    else:
        sec_mean, sec_findings = 100, 0

    # 一致性
    cons = consistency(codes) if runs >= 2 else None

    # 效率
    toks = sum(token_estimate(c) for c in codes) + sum(token_estimate(case["idea"]) for _ in codes)
    mean_dt = sum(times) / len(times) if times else 0

    return {
        "id": case["id"],
        "category": case["category"],
        "task_type": case["task_type"],
        "runs": n,
        "valid_rate": c_valid / n,
        "pass@k": passk,
        "pass@1_ci95": [round(p1_lo, 3), round(p1_hi, 3)],
        "mock_rate": sum(mocks) / n,
        "security_score": round(sec_mean, 1),
        "security_findings": sec_findings,
        "consistency": round(cons, 3) if cons is not None else None,
        "mean_latency_s": round(mean_dt, 2),
        "token_est": toks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default=LLM_PROVIDER)
    ap.add_argument("--only", default=None, help="只跑某个 case id")
    ap.add_argument("--report", default=os.path.join(HERE, "_eval_report.json"), help="报告路径（CI 使用临时目录）")
    args = ap.parse_args()

    if not provider_available(args.model):
        print(f"[WARN] provider {args.model} 不可用（缺 key/被拦截），结果将全为 mock。")
    print(f"模型={args.model}  runs={args.runs}\n")

    with open(os.path.join(HERE, "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    if not cases:
        print("无匹配 case"); return

    results = []
    for case in cases:
        r = eval_case(case, args.model, args.runs)
        results.append(r)
        c = "✅" if r["valid_rate"] >= 0.99 else ("⚠️" if r["valid_rate"] > 0 else "❌")
        print(f"{c} {case['id']:<24} 有效={r['valid_rate']:.2f} "
              f"安全={r['security_score']:.0f} 一致={r['consistency']} "
              f"延迟={r['mean_latency_s']}s mock={r['mock_rate']:.2f}")

    # 汇总
    vrates = [r["valid_rate"] for r in results]
    p1s = [r["pass@k"]["pass@1"] for r in results]
    # 结构化输出有效性 gate：非 explain 类有效率均值 >= 0.98 视为通过
    sov = structured_output_validity(results)
    sov_pt, sov_lo, sov_hi = bootstrap_ci([1 if r["valid_rate"] >= 0.98 else 0 for r in results
                                          if r["task_type"] != "explain"])
    gate = {
        "structured_output_validity": round(sov, 3),
        "structured_output_gate_pass": sov >= 0.98,
        "valid_rate_ci95": [round(sov_lo, 3), round(sov_hi, 3)],
        "all_cases_pass": all(r["valid_rate"] >= 0.99 for r in results),
        "min_security": min((r["security_score"] for r in results), default=100),
    }
    overall = {
        "model": args.model,
        "runs_per_case": args.runs,
        "n_cases": len(results),
        "pass@1(有效率)_mean": round(mean(vrates), 3),
        "pass@k_mean": round(mean(p1s), 3),
        "mean_security": round(sum(r["security_score"] for r in results) / len(results), 1),
        "total_security_findings": sum(r["security_findings"] for r in results),
        "mean_latency_s": round(sum(r["mean_latency_s"] for r in results) / len(results), 2),
        "mean_mock_rate": round(sum(r["mock_rate"] for r in results) / len(results), 3),
        "consistency_avg": (round(sum(r["consistency"] for r in results if r["consistency"] is not None) /
                             max(1, sum(1 for r in results if r["consistency"] is not None)), 3)
                            if any(r["consistency"] is not None for r in results) else None),
    }
    strata = {
        "by_category": strata_aggregate(results, "category"),
        "by_task_type": strata_aggregate(results, "task_type"),
    }
    # 版本审计链：便于回归对比（harness/model/item 版本）
    import subprocess
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        git_sha = "unknown"
    audit = {
        "harness_version": HARNESS_VERSION,
        "model": args.model,
        "runs_per_case": args.runs,
        "n_cases": len(results),
        "git_sha": git_sha,
        "timestamp_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "case_ids": [r["id"] for r in results],
    }
    out = {
        "summary": overall,
        "gates": gate,
        "strata": strata,
        "audit": audit,
        "cases": results,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n=== 汇总 ===")
    for k, v in overall.items():
        print(f"  {k}: {v}")
    print("\n=== 质量门禁 (Gate) ===")
    print(f"  结构化输出有效性: {gate['structured_output_validity']} "
          f"(gate {'PASS' if gate['structured_output_gate_pass'] else 'FAIL'} >=0.98)")
    print(f"  全部 case 通过: {gate['all_cases_pass']} | 最低安全分: {gate['min_security']}")
    print(f"\n报告已写入 {args.report}")


if __name__ == "__main__":
    main()
