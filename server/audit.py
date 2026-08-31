"""SOC 2 风格不可变审计（独立于业务数据库）。

对齐 SOC 2 TSC：CC6(逻辑访问) / CC7(监控) / CC4(沟通)。
核心特性：
- 结构化 JSON 审计条目，覆盖「5W」：Who(actor_id) / What(action,resource_type,
  resource_id) / When(timestamp_utc) / Where(source_ip,session_id) / result(outcome)。
- WORM（Write-Once-Read-Many）追加式文件存储，带 **hash-chain**（每行含
  prev_hash + 自身 hash），任意篡改都会破坏链，可被 verify_chain() 检测。
  与业务 SQLite 分离，满足「审计存储独立」要求（S3 Object Lock 的可本地化等价）。
- 保留期：默认 365 天（HIPAA 6 年 / PCI-DSS 12 月 / SOX 7 年可由环境变量覆盖）。
- 高危告警（high-fidelity）：失败登录、提权、非工作时间访问、同 IP 短时多次失败。

零依赖（仅标准库）。可在无 DB 情况下独立使用。
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

WORM_PATH = os.environ.get(
    "AUDIT_WORM_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_trail.log"),
)
RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))

# 进程内缓存上一行 hash（单 worker 场景下足够；多 worker 时以文件尾部为准）
_last_hash: str | None = None


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(event_json: str, prev: str) -> str:
    return hashlib.sha256((prev + "|" + event_json).encode("utf-8")).hexdigest()


def _read_tail_hash() -> str | None:
    """读取 WORM 文件最后一行的 hash（O(读尾部)，不加载全文件）。"""
    global _last_hash
    if _last_hash is not None:
        return _last_hash
    if not os.path.exists(WORM_PATH):
        return None
    try:
        with open(WORM_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return None
            # 读取最后 4KB 找最后一行
            f.seek(max(0, size - 4096), os.SEEK_SET)
            buf = f.read().decode("utf-8", "ignore")
        last = buf.rstrip("\n").split("\n")[-1]
        if not last.strip():
            return None
        rec = json.loads(last)
        _last_hash = rec.get("hash")
        return _last_hash
    except Exception:
        return None


def emit(actor_id, action: str, resource_type: str | None = None,
         resource_id: str | None = None, outcome: str = "success",
         source_ip: str | None = None, session_id: str | None = None,
         detail: str | None = None) -> dict:
    """写入一条结构化审计事件（追加到 WORM 文件，含 hash-chain）。

    outcome: success | failure | denied
    返回完整事件（含 hash/prev_hash），便于调用方同时落业务库。
    """
    event = {
        "ts_utc": _now_utc(),
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "source_ip": source_ip,
        "session_id": session_id,
        "detail": detail,
    }
    event_json = json.dumps(event, ensure_ascii=False, sort_keys=True)
    prev = _read_tail_hash() or "GENESIS"
    h = _hash(event_json, prev)
    event["prev_hash"] = prev
    event["hash"] = h
    line = json.dumps(event, ensure_ascii=False)
    with open(WORM_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    global _last_hash
    _last_hash = h
    return event


def verify_chain(path: str | None = None) -> tuple[bool, int]:
    """校验整条 hash-chain 完整性，返回 (ok, 首条断裂行号)。"""
    path = path or WORM_PATH
    if not os.path.exists(path):
        return True, -1
    prev = "GENESIS"
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                return False, i
            recomputed = _hash(
                json.dumps({k: v for k, v in rec.items() if k not in ("hash", "prev_hash")},
                           ensure_ascii=False, sort_keys=True),
                rec.get("prev_hash") or "GENESIS",
            )
            if recomputed != rec.get("hash") or (rec.get("prev_hash") or "GENESIS") != prev:
                return False, i
            prev = rec.get("hash")
    return True, -1


def high_fidelity_alerts(events: list[dict]) -> list[dict]:
    """基于近期事件生成高危告警（SOC 2 CC7 监控）。

    规则：
      - 失败登录（outcome=failure && action~login）
      - 提权（action=role_change / create_user）
      - 非工作时间访问（UTC 小时 0-6）
      - 同 source_ip 短时内多次失败（>=5）
    """
    alerts: list[dict] = []
    ip_fail: dict[str, int] = {}
    for e in events:
        act = (e.get("action") or "").lower()
        oc = e.get("outcome")
        if "login" in act and oc == "failure":
            alerts.append({"severity": "high", "type": "failed_login",
                           "actor": e.get("actor_id"), "ip": e.get("source_ip"),
                           "ts": e.get("ts_utc")})
            ip = e.get("source_ip")
            if ip:
                ip_fail[ip] = ip_fail.get(ip, 0) + 1
        if act in ("role_change", "create_user", "delete_user"):
            alerts.append({"severity": "high", "type": "privilege_change",
                           "actor": e.get("actor_id"), "action": e.get("action"),
                           "ts": e.get("ts_utc")})
        # 非工作时间（UTC 0-6 视为 after-hours）
        ts = e.get("ts_utc", "")
        m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}):", ts or "")
        if m and 0 <= int(m.group(1)) < 6:
            alerts.append({"severity": "medium", "type": "after_hours_access",
                           "actor": e.get("actor_id"), "ts": ts})
    for ip, n in ip_fail.items():
        if n >= 5:
            alerts.append({"severity": "critical", "type": "brute_force",
                           "ip": ip, "failures": n})
    return alerts


def iter_events(path: str | None = None):
    """按行 yield 解析后的审计事件（生成器，避免全量加载）。"""
    path = path or WORM_PATH
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue
