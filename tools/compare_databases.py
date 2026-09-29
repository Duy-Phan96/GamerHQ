"""Compare consistent GamerHQ snapshots read-only; output counts, never row values."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


TABLES = ('games', 'deleted_games', 'settings', 'managed_roles', 'managed_message_content',
          'managed_message_audit', 'support_tickets', 'ticket_audit', 'lfg_events',
          'lfg_event_members', 'lfg_event_messages', 'lfg_voice_notifications', 'lfg_time_proposals',
          'suggestions', 'temp_voice_channels', 'curated_deals', 'processed_affiliate_deals',
          'twitch_connections', 'twitch_live_deliveries', 'streamer_applications',
          'streamer_profiles', 'streamer_channels')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=lambda v: v.hex(),
                                     separators=(',', ':')).encode()).hexdigest()


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def inspect_database(path):
    """One read transaction; internal hashes are used for comparison, not displayed."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('An existing regular SQLite snapshot is required')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        if not {'games', 'settings'} <= set(tables):
            raise ValueError('Not a GamerHQ runtime snapshot')
        result = dict(schema_version=conn.execute('PRAGMA user_version').fetchone()[0], counts={}, rows={}, schema={})
        result['objects'] = sorted(digest(list(row)) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('index','view','trigger')"))
        columns = {}
        for table in tables:
            info = list(conn.execute(f'PRAGMA table_info({quote(table)})'))
            names = [r[1] for r in info]
            columns[table] = set(names)
            keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            rows = {}
            for values in conn.execute(f'SELECT * FROM {quote(table)}'):
                row = dict(zip(names, values))
                identity = digest([row[k] for k in keys]) if keys else digest(row)
                rows.setdefault(identity, []).append(digest(row))
            result['rows'][table] = {k: sorted(v) for k, v in rows.items()}
            result['schema'][table] = [list(r) for r in info]
            if table in TABLES:
                result['counts'][table] = sum(len(v) for v in rows.values())
        def count(label, table, required, condition):
            result['counts'][label] = (conn.execute(f'SELECT COUNT(*) FROM {quote(table)} WHERE {condition}').fetchone()[0]
                                        if set(required) <= columns.get(table, set()) else None)
        count('active_games', 'games', ['active'], 'active=1')
        count('visible_games', 'games', ['selectable'], 'selectable=1')
        count('selector_games', 'games', ['active', 'selectable', 'role_id'], 'active=1 AND selectable=1 AND role_id IS NOT NULL AND role_id != 0')
        count('game_role_mappings', 'games', ['role_id'], 'role_id IS NOT NULL AND role_id != 0')
        count('visible_games_without_role', 'games', ['selectable', 'role_id'], 'selectable=1 AND (role_id IS NULL OR role_id=0)')
        fields = [f for f in columns['games'] if f == 'category_id' or f.endswith('_channel_id')]
        count('game_area_mappings', 'games', fields, ' OR '.join(f'{quote(f)} IS NOT NULL' for f in fields) or '0')
        count('open_tickets', 'support_tickets', ['status'], "upper(status) NOT IN ('CLOSED','DELETED')")
        count('active_lobbies', 'lfg_events', ['status'], "lower(status) IN ('scheduled','active','live')")
        count('managed_channel_mappings', 'settings', ['key', 'value'], "(key LIKE 'managed_channel:%' OR key LIKE 'managed_category:%') AND value IS NOT NULL AND value != ''")
        count('managed_message_mappings', 'settings', ['key', 'value'], "(key LIKE '%message%') AND value IS NOT NULL AND value != ''")
        result['counts']['other_tables'] = len(set(tables) - set(TABLES))
        result['counts']['schema_objects'] = len(result['objects'])
        result['fingerprint'] = digest([result['schema_version'], result['schema'], result['rows'], result['objects']])
        return result


def compare(source, production):
    left, right = inspect_database(source), inspect_database(production)
    differences = {}
    for table in sorted(set(left['rows']) | set(right['rows'])):
        a, b = left['rows'].get(table, {}), right['rows'].get(table, {})
        item = dict(source_only=sum(len(a[k]) for k in a.keys() - b.keys()),
                    production_only=sum(len(b[k]) for k in b.keys() - a.keys()),
                    changed=sum(a[k] != b[k] for k in a.keys() & b.keys()),
                    schema_changed=left['schema'].get(table) != right['schema'].get(table))
        # Unknown names can be private; aggregate them without exposing identifiers.
        label = table if table in TABLES else 'other_tables'
        if label in differences:
            differences[label] = {key: differences[label][key] + value for key, value in item.items()}
        else:
            differences[label] = item
    differences['schema_objects'] = dict(source_only=len(set(left['objects']) - set(right['objects'])),
        production_only=len(set(right['objects']) - set(left['objects'])), changed=0, schema_changed=False)
    conflicts = sum(d['production_only'] + d['changed'] + bool(d['schema_changed']) for d in differences.values())
    token = digest([left['fingerprint'], right['fingerprint']])
    recommendation = ('Keep production authoritative; review a scoped recovery/merge on copies. Do not replace automatically.'
                      if conflicts else 'Source may be a suitable basis after guild identity and mappings are reviewed; replacement still requires backup and explicit confirmation.')
    return dict(source=dict(schema_version=left['schema_version'], counts=left['counts']),
                production=dict(schema_version=right['schema_version'], counts=right['counts']),
                differences=differences, potential_conflicts=conflicts, review_token=token,
                recommendation=recommendation)


def render(report):
    lines = ['GamerHQ database comparison (read-only)', 'LOCAL / SOURCE versus PRODUCTION',
             f"Schema: {report['source']['schema_version']} / {report['production']['schema_version']}"]
    for name in sorted(set(report['source']['counts']) | set(report['production']['counts'])):
        values = [side['counts'].get(name) for side in (report['source'], report['production'])]
        lines.append(f"{name}: " + ' / '.join(str(v) if v is not None else 'unavailable' for v in values))
    lines.append('POTENTIAL CONFLICTS (identity/content differences, not proof of creation time)')
    for name, delta in report['differences'].items():
        if any(delta.values()):
            lines.append(f"{name}: source-only={delta['source_only']}, production-only={delta['production_only']}, changed={delta['changed']}, schema-changed={bool(delta['schema_changed'])}")
    lines += [f"Potential conflicts: {report['potential_conflicts']}", 'RECOMMENDATION: ' + report['recommendation'],
              'Review token: ' + report['review_token'],
              'No changes made. No row contents, credentials or Discord IDs printed.']
    for name in ('source', 'production'):
        counts = report[name]['counts']
        diagnosis = ('Game catalog is empty.' if counts.get('games') == 0 else
                     'Selection schema unavailable; rehearse migrations on a copy.' if counts.get('visible_games') is None else
                     'Games exist but none are selectable; the Select Games menu will be empty.' if counts.get('visible_games') == 0 else
                     'Visible games lack role mappings; the Select Games menu will be empty.' if counts.get('selector_games') == 0 else
                     'Selectable games exist; verify the running DB path and Discord role access.')
        lines.append(name.title() + ' selection diagnosis: ' + diagnosis)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('production', type=Path)
    args = parser.parse_args()
    try:
        print(render(compare(args.source, args.production)))
    except (OSError, ValueError, sqlite3.Error):
        print('Comparison unavailable: check snapshot paths, integrity and schema privately. No values printed.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
