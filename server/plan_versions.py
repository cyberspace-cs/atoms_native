"""产品方案版本发展历史（server/plan_versions.py）。

把仓库 docs/plan/ 下的版本索引与快照变成可编程的数据源：
  - list_versions():  解析 版本发展历史.md 的索引表（时间倒序，最新在上）
  - read_snapshot():  读取指定版本快照的完整 markdown 原文

安全约束：快照文件名必须匹配白名单正则，且解析后必须落在 history 目录内
（防路径穿越，对齐 OWASP LLM06 过度代理的文件访问收敛）。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = REPO_ROOT / "docs" / "plan"
HISTORY_DIR = PLAN_DIR / "history"

# 快照文件名白名单：产品方案_v1.0_2026-09-02_初版.md
_SNAPSHOT_RE = re.compile(r"^产品方案_v[\w.\-]+_\d{4}-\d{2}-\d{2}_.+\.md$")
_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*.+?\|\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _strip_comments(text: str) -> str:
    """去掉 HTML 注释块，避免把「下一行模板」的示例行当成真实版本。"""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _clean(cell: str) -> str:
    """单元格清理：去加粗标记与链接语法，保留可读文本。"""
    cell = re.sub(r"\*\*(.+?)\*\*", r"\1", cell.strip())
    m = _LINK_RE.search(cell)
    if m:
        cell = cell.replace(m.group(0), m.group(1)).strip()
    return cell


def list_versions() -> list[dict]:
    """解析版本发展历史.md 索引表，返回 [{version, date, topic, summary, snapshot}]。

    表格时间倒序（最新在上），解析结果保持原文档顺序；解析失败返回空列表，
    绝不让文档展示功能拖垮主服务。
    """
    index_file = PLAN_DIR / "版本发展历史.md"
    if not index_file.exists():
        return []
    try:
        text = _strip_comments(index_file.read_text(encoding="utf-8"))
    except OSError:
        return []
    versions = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        version_raw, date_raw, topic_raw, summary_raw = m.groups()
        # 只认形如 v1.0 的版本号行，其余表头/杂行跳过
        vm = re.match(r"v[\w.\-]+", _clean(version_raw))
        if not vm:
            continue
        # 快照链接：取最后一个 markdown 链接的目标路径，归一化为纯文件名
        links = _LINK_RE.findall(line)
        snapshot = links[-1][1].strip() if links else None
        if snapshot and "/" in snapshot:
            snapshot = snapshot.rsplit("/", 1)[-1]
        versions.append({
            "version": vm.group(0),
            "date": _clean(date_raw),
            "topic": _clean(topic_raw),
            "summary": _clean(summary_raw),
            "snapshot": snapshot,
        })
    return versions


def milestones() -> list[dict]:
    """解析「迭代路径」小节：真实 git 提交时间线的里程碑，返回 [{time, milestone, note}]。"""
    index_file = PLAN_DIR / "版本发展历史.md"
    if not index_file.exists():
        return []
    try:
        text = _strip_comments(index_file.read_text(encoding="utf-8"))
    except OSError:
        return []
    m = re.search(r"##\s*迭代路径(.*?)(?:\n##\s|\Z)", text, flags=re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        r = re.match(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", line)
        if not r or r.group(1).strip() == "时间":
            continue
        out.append({"time": _clean(r.group(1)),
                    "milestone": _clean(r.group(2)),
                    "note": _clean(r.group(3))})
    return out


def snapshot_path(name: str) -> Path | None:
    """校验快照文件名并返回绝对路径；非法或越界返回 None（防路径穿越）。"""
    if not name or not _SNAPSHOT_RE.match(name):
        return None
    p = (HISTORY_DIR / name).resolve()
    try:
        p.relative_to(HISTORY_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def read_snapshot(name: str) -> str | None:
    """读取快照 markdown 原文；不存在/非法返回 None。"""
    p = snapshot_path(name)
    if p is None:
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None
