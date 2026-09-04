"""Atomic version delivery, separated from LLM orchestration and SSE transport."""
from contextlib import closing

from agent.pipeline import _valid_html
from database import get_conn


class GenerationConflict(Exception):
    pass


def commit_generation(project, result, message=None):
    """Commit an approved/downgraded generation against its captured parent.

    Return None for non-deliverable results. An optimistic parent check inside
    BEGIN IMMEDIATE prevents stale generations from overwriting rollbacks.
    """
    if result.get('status') not in ('success', 'degraded'):
        return None
    if not _valid_html(result.get('code', '')):
        raise ValueError('Cannot commit invalid HTML')
    if result['status'] == 'success' and (result.get('mock') or result.get('verdict') != 'approve'):
        raise ValueError('Cannot commit unapproved output as success')
    if result['status'] == 'degraded' and not result.get('mock'):
        raise ValueError('Degraded output must be marked mock')
    with closing(get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT current_version FROM projects WHERE id=? AND user_id=?',
                               (project['id'], project['user_id'])).fetchone()
        if not current or current['current_version'] != project['current_version']:
            raise GenerationConflict('项目版本已变化，请刷新后重试；本次结果未覆盖现有版本。')
        if message is not None and project['current_version']:
            base = conn.execute('SELECT code FROM versions WHERE id=? AND project_id=?',
                                (project['current_version'], project['id'])).fetchone()
            if base and base['code'].strip() == result['code'].strip():
                return None
        vno = conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM versions WHERE project_id=?',
                           (project['id'],)).fetchone()[0]
        note = '离线模板' if result['mock'] else ('精修：' + message[:30] if message is not None else '初版生成')
        vid = conn.execute(
            'INSERT INTO versions(project_id,version_no,code,model_used,note,security_score,'
            'status,mock,parent_version,call_count) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (project['id'], vno, result['code'], result['model'], note,
             result.get('security', {}).get('score'), result['status'], int(result['mock']),
             project['current_version'], result['call_count'])).lastrowid
        conn.execute("UPDATE projects SET current_version=?,spec_json=?,arch_json=?,status='ready',"
                     "updated_at=datetime('now') WHERE id=?",
                     (vid, result['spec'], result['arch'], project['id']))
        if message is not None:
            conn.execute('INSERT INTO messages(project_id,role,content) VALUES(?,?,?)',
                         (project['id'], 'user', message))
        return vid
