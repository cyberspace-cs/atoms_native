"""多租户分布式限流 + 并发守卫（对齐企业实践）。

对齐：多租户分布式限流 —— Redis Cluster + Lua 原子脚本、Key=
`rl:{tenant}:{endpoint}:{rule}:{dim}`、fail-open 默认、灰度
DRAFT→SHADOW→ENFORCE(%)→ROLLBACK。

特性：
- 令牌桶（Token Bucket）：恒定时间、支持突发（burst）、平滑限流。
- 多维度：tenant(租户) × endpoint(接口) × 可选 dim(如模型)。
- 灰度：RATELIMIT_MODE=enforce 真正拦截；shadow/draft 只观测不拦截（记录但不拒）。
- fail-open：Redis 不可用 / Lua 执行异常时默认放行（避免雪崩），并发守卫同理。
- 优雅降级：未安装 redis 或连不上时，自动退化为进程内令牌桶（单实例语义）。
- 并发守卫：替换原进程内 _active，用 Redis SET NX EX 实现跨进程单任务互斥；
  同样 fail-open + 进程内降级。

零强依赖：redis 为可选，缺失时走进程内实现。
"""
import os
import time
from typing import Optional

# 端点速率配置：(capacity 令牌容量, period 秒)。rate = capacity/period 平滑补充。
ENDPOINT_RATES = {
    "generate": (20, 3600),
    "refine": (20, 3600),
    "race": (10, 3600),
    "default": (60, 3600),
}

# 灰度模式：enforce 拦截 / shadow 仅观测 / draft 仅观测 / off 全放行
MODE = os.environ.get("RATELIMIT_MODE", "enforce").lower()
REDIS_URL = os.environ.get("REDIS_URL", "")  # 例如 redis://localhost:6379/0
CAPACITY_OVERRIDE = int(os.environ.get("RATELIMIT_CAPACITY", "0")) or None

_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local period = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local now = tonumber(ARGV[5])
local ttl = tonumber(ARGV[6])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
local delta = (now - ts) * rate / period
tokens = math.min(capacity, tokens + delta)
local allowed = 0
if tokens >= cost then tokens = tokens - cost; allowed = 1 end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)
return {allowed, tokens}
"""

_redis = None
_redis_dead_until = 0.0
_REDIS_RETRY_S = 30.0


def _get_redis():
    """惰性获取 Redis 连接（导入时不强制依赖）。返回 client|None。

    两个关键点（都是上线验证时踩出来的）：
    - 成功后必须缓存：否则每次限流判定都会重建 client + ping，
      白白多一次网络往返，连接池随请求数不断膨胀。
    - 失败只「冷却」不「判死」：Redis 抖动/重启后能自动回到分布式限流，
      避免一次失败就永久退化成进程内限流（沉默降级，且看不出来）。
    """
    global _redis, _redis_dead_until
    if _redis is not None:
        return _redis
    if not REDIS_URL:
        return None
    if time.time() < _redis_dead_until:
        return None  # 冷却期内不再反复建连
    try:
        import redis  # 可选依赖
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.3,
                                      socket_timeout=0.3, decode_responses=True)
        client.ping()
        _redis = client
        return client
    except Exception:
        _redis_dead_until = time.time() + _REDIS_RETRY_S
        return None


# ---------------- 进程内降级令牌桶 ----------------
_inproc: dict[str, dict] = {}


def _inproc_allow(key: str, capacity: int, period: float, cost: int) -> bool:
    now = time.time()
    b = _inproc.get(key)
    if b is None:
        b = _inproc[key] = {"tokens": float(capacity), "ts": now}
    delta = (now - b["ts"]) * (capacity / period)
    b["tokens"] = min(capacity, b["tokens"] + delta)
    b["ts"] = now
    if b["tokens"] >= cost:
        b["tokens"] -= cost
        return True
    return False


def allow(tenant_id, endpoint: str, dim: str = "", cost: int = 1) -> dict:
    """限流判定。返回 {allowed, mode, backend, remaining, shadow}。

    tenant_id: 租户/用户标识；endpoint: 接口名；dim: 额外维度（如模型 id）。
    fail-open：任何异常都放行。
    """
    cap, period = ENDPOINT_RATES.get(endpoint, ENDPOINT_RATES["default"])
    if CAPACITY_OVERRIDE:
        cap = CAPACITY_OVERRIDE
    rate = cap / period
    shadow = MODE in ("shadow", "draft")

    key = f"rl:{tenant_id}:{endpoint}:{endpoint}:{dim}" if dim else f"rl:{tenant_id}:{endpoint}"

    client = _get_redis()
    if client is not None:
        try:
            ttl = int(period * 2) + 60
            res = client.eval(_LUA, 1, key, cap, rate, period, cost, time.time(), ttl)
            allowed = bool(res[0])
            remaining = res[1]
            backend = "redis"
        except Exception:
            # fail-open：Redis 抖动时放行，不雪崩
            return {"allowed": True, "mode": MODE, "backend": "redis_fail_open",
                    "remaining": cap, "shadow": shadow}
    else:
        allowed = _inproc_allow(key, cap, period, cost)
        remaining = None
        backend = "inproc"

    # 灰度：shadow/draft 不真正拦截
    if shadow:
        return {"allowed": True, "mode": MODE, "backend": backend,
                "remaining": remaining, "shadow": True}
    return {"allowed": allowed, "mode": MODE, "backend": backend,
            "remaining": remaining, "shadow": False}


def status() -> dict:
    """限流后端自省：让运维一眼看出是否处于降级状态。

    避免「以为在跑分布式令牌桶、其实已悄悄退回进程内」的沉默降级。
    """
    client = _get_redis()
    backend = "redis" if client is not None else "inproc"
    return {
        "mode": MODE,
        "backend": backend,
        "redis_url_set": bool(REDIS_URL),
        "redis_reachable": client is not None,
        "capacity_override": CAPACITY_OVERRIDE,
        "endpoint_rates": {k: {"capacity": v[0], "period_s": v[1]}
                           for k, v in ENDPOINT_RATES.items()},
    }


# ---------------- 并发守卫（跨进程单任务互斥） ----------------
_inproc_active: set = set()


def acquire(tenant_id, ttl: int = 1800) -> bool:
    """获取该租户的生成/精修/竞速锁。成功返回 True。fail-open。"""
    key = f"lock:{tenant_id}"
    client = _get_redis()
    if client is not None:
        try:
            ok = client.set(key, "1", nx=True, ex=ttl)
            return bool(ok)
        except Exception:
            pass
    # 降级：进程内集合
    if tenant_id in _inproc_active:
        return False
    _inproc_active.add(tenant_id)
    return True


def release(tenant_id):
    key = f"lock:{tenant_id}"
    client = _get_redis()
    if client is not None:
        try:
            client.delete(key)
        except Exception:
            pass
    _inproc_active.discard(tenant_id)
