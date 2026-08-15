"""Full step-H scoring prototype: every stage the spec describes, so the
remaining parameters are tuned against the real algorithm rather than a proxy.

Stages: play weight -> per-version inputs -> saturate+weight -> bucket
baselines (median inputs, scored once) -> bounded shrinkage -> aggregation
-> display transform.
"""
import os
import statistics
import sys
from datetime import datetime, timedelta

# Lives under docs/, so put the project root on the path: run it from anywhere as
#   venv/bin/python docs/scoring/tuning_prototype.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db  # noqa: E402

END = datetime.fromisoformat('2026-08-14T00:00:00+00:00')

# The settled values (docs/specs/scoring-H.md §10.1). Changing any of these
# invalidates every stored score -- see the spec's §10 warning.
P = dict(
    REASON_END_WEIGHT={},                 # struck: the listen fraction already prices skips
    MIN_EXPOSURE_DAYS=14,
    K_RATE=0.5, K_MEM=4.0, K_TEN=2.0,
    W_RATE=0.40, W_MEM=0.35, W_TEN=0.25,  # structural, NOT fitted -- see spec §4.4
    K_SHRINK=3.0, SHRINK_MAX=0.5,
    P_AGG=2.5, TAIL_FLOOR=1.0,            # TAIL_FLOOR is deliberately inert
    FEATURED_WEIGHT=0.6, SUBTIER_W=0.05,
    RECENT_WINDOW_DAYS=90, RECENT_ALLTIME_BLEND=0.15,
    SCALE=100.0, GAMMA=0.5,
)


def sat(x, k):
    return x / (x + k)


def fetch(conn, recent, prm):
    """Per-version raw inputs (W, M, T, exposure) for one horizon."""
    win = (END - timedelta(days=prm['RECENT_WINDOW_DAYS'])).strftime('%Y-%m-%dT%H:%M:%SZ')
    pf = f"AND p.ts >= '{win}'" if recent else ''
    af = f"AND m.added_at >= '{win}'" if recent else ''
    gf = (f"AND gp.ordinal IN (SELECT g.ordinal FROM generation g JOIN membership mm "
          f"ON mm.playlist_id=g.playlist_id AND mm.removed_at IS NULL GROUP BY g.ordinal "
          f"HAVING MIN(mm.added_at) >= '{win}')") if recent else ''
    rw = prm.get('REASON_END_WEIGHT') or {}
    wexpr = "MIN(p.ms_played*1.0/NULLIF(t.duration_ms,0),1.0)"
    if rw:   # struck by default: the listen fraction already prices skips
        case = ' '.join(f"WHEN '{k}' THEN {v}" for k, v in rw.items())
        wexpr += f" * (CASE p.reason_end {case} ELSE 1.0 END)"
    q = f"""
    WITH mem AS (SELECT tg.version_id, COUNT(*) n FROM membership m
                 JOIN track_group tg ON tg.track_id=m.track_id
                 WHERE m.removed_at IS NULL {af} GROUP BY tg.version_id),
     ten AS (SELECT gp.version_id, COUNT(DISTINCT gp.ordinal) n FROM generation_presence gp
             WHERE 1=1 {gf} GROUP BY gp.version_id),
     pl AS (SELECT tg.version_id, SUM({wexpr}) w, MIN(p.ts) fp FROM play p
            JOIN played_uri_track x ON x.uri=p.spotify_track_uri
            JOIN track t ON t.track_id=x.track_id
            JOIN track_group tg ON tg.track_id=t.track_id
            WHERE 1=1 {pf} GROUP BY tg.version_id),
     ad AS (SELECT tg.version_id, MIN(m.added_at) a FROM membership m
            JOIN track_group tg ON tg.track_id=m.track_id GROUP BY tg.version_id),
     mem_all AS (SELECT tg.version_id, COUNT(*) n FROM membership m
                 JOIN track_group tg ON tg.track_id=m.track_id
                 WHERE m.removed_at IS NULL GROUP BY tg.version_id),
     ten_all AS (SELECT version_id, COUNT(DISTINCT ordinal) n FROM generation_presence GROUP BY version_id)
    SELECT DISTINCT tg.version_id,
           COALESCE(mem.n,0) M, COALESCE(ten.n,0) T, COALESCE(pl.w,0.0) W,
           COALESCE(mem_all.n,0) M_all, COALESCE(ten_all.n,0) T_all,
           MIN(COALESCE(pl.fp,'9999'), COALESCE(ad.a,'9999')) fo
    FROM track_group tg
    LEFT JOIN mem ON mem.version_id=tg.version_id
    LEFT JOIN ten ON ten.version_id=tg.version_id
    LEFT JOIN pl  ON pl.version_id=tg.version_id
    LEFT JOIN ad  ON ad.version_id=tg.version_id
    LEFT JOIN mem_all ON mem_all.version_id=tg.version_id
    LEFT JOIN ten_all ON ten_all.version_id=tg.version_id"""
    out = {}
    for r in conn.execute(q):
        fo = r['fo']
        if recent and fo != '9999' and fo < win:
            fo = win
        E = prm['MIN_EXPOSURE_DAYS'] if fo == '9999' else max((END - datetime.fromisoformat(fo)).days,
                                                              prm['MIN_EXPOSURE_DAYS'])
        out[r['version_id']] = dict(W=r['W'], M=r['M'], T=r['T'], R=r['W'] / E * 30,
                                    bucket=('C' if r['T_all'] > 0 else 'B' if r['M_all'] > 0 else 'A'))
    return out


def raw(v, prm):
    return (prm['W_RATE'] * sat(v['R'], prm['K_RATE'])
            + prm['W_MEM'] * sat(v['M'], prm['K_MEM'])
            + prm['W_TEN'] * sat(v['T'], prm['K_TEN']))


def score_all(rows, prm):
    """raw -> bucket baselines (median inputs, scored once) -> bounded shrinkage."""
    for v in rows.values():
        v['raw'] = raw(v, prm)
    base = {}
    for b in 'ABC':
        mem = [v for v in rows.values() if v['bucket'] == b]
        if not mem:
            base[b] = 0.0
            continue
        synth = {k: statistics.median([v[k] for v in mem]) for k in ('R', 'M', 'T')}
        base[b] = raw(synth, prm)
    out = {}
    for vid, v in rows.items():
        pull = min(prm['K_SHRINK'] / (v['W'] + prm['K_SHRINK']), prm['SHRINK_MAX'])
        out[vid] = v['raw'] + pull * (base[v['bucket']] - v['raw'])
    return out, base


def combine(scores, p, floor, weights=None):
    if not scores:
        return 0.0
    smax = max(scores) or 1.0
    w = [max(s / smax, floor) * (weights[i] if weights else 1.0) for i, s in enumerate(scores)]
    tot = sum(w)
    if tot == 0:
        return 0.0
    return (sum(w[i] * scores[i] ** p for i in range(len(scores))) / tot) ** (1 / p)


def display(s, prm):
    return prm['SCALE'] * (max(s, 0.0) ** prm['GAMMA'])


# ---------------------------------------------------------------- validation set
def collections(conn):
    ids = {'v37': conn.execute("SELECT playlist_id FROM snapshot WHERE name='v37.2.1'").fetchone()[0],
           'schur': conn.execute("SELECT artist_id FROM artist WHERE name='Schur' LIMIT 1").fetchone()[0],
           'wknd': conn.execute("SELECT artist_id FROM artist WHERE name='The Weeknd' LIMIT 1").fetchone()[0]}
    PL = ('SELECT DISTINCT tg.version_id FROM track_group tg JOIN membership m ON m.track_id=tg.track_id '
          'WHERE m.playlist_id=? AND m.removed_at IS NULL')
    AR = ('SELECT DISTINCT tg.version_id FROM track_group tg JOIN resolved_track_artist rta '
          'ON rta.track_id=tg.track_id WHERE rta.artist_id=?')
    return [('My playlist #134', PL, '7mWfK1TSXu6wD7qCV5WsaJ', 'TOP'),
            ('half•alive', AR, '7sOR7gk6XUlGnxj3p9F54k', 'TOP'),
            ('v37.2.1', PL, ids['v37'], 'HIGH'),
            ('My playlist #149', PL, '2vEu5Q0uJDBxgdNU7V9SoK', 'HIGH'),
            ('Indie Rock Mix', PL, '1T5ntM6oTognvuwWAad2rD', 'MID'),
            ('Schur', AR, ids['schur'], 'MID'),
            ('Phoebe Bridgers', AR, '1r1uxoy19fzMxunt3ONAkG', 'MID'),
            ('The Weeknd', AR, ids['wknd'], 'LOW'),
            ('1984 (noah)', PL, '474CNZAHx7kDPetrvDBGqC', 'LOW'),
            ('k-poop', PL, '0qDdsg0DcSNcJOHyxsSdGO', 'LOW'),
            ('airpods', PL, '4mvmZRmDxdLLiSRhLYlQwH', 'BOTTOM')]


TIER = {'TOP': 0, 'HIGH': 1, 'MID': 2, 'LOW': 3, 'BOTTOM': 4}


def evaluate(conn, prm, rows=None, members=None):
    rows = rows if rows is not None else fetch(conn, False, prm)
    sc, base = score_all(rows, prm)
    if members is None:
        members = {nm: [r[0] for r in conn.execute(q, (pid,))] for nm, q, pid, _ in collections(conn)}
    tiers = {nm: t for nm, _, _, t in collections(conn)}
    comp = {nm: combine([sc[v] for v in vs if v in sc], prm['P_AGG'], prm['TAIL_FLOOR'])
            for nm, vs in members.items()}
    order = sorted(comp.items(), key=lambda kv: -kv[1])
    inv = pairs = 0
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            a, b = TIER[tiers[order[i][0]]], TIER[tiers[order[j][0]]]
            if a != b:
                pairs += 1
                if a > b:
                    inv += 1
    return inv, pairs, comp, sc, base


if __name__ == '__main__':
    conn = db.connect()
    inv, pairs, comp, sc, base = evaluate(conn, P)
    print(f'inversions {inv}/{pairs}   bucket baselines ' +
          ' '.join(f'{k}={v:.3f}' for k, v in base.items()))
    print()
    for nm, v in sorted(comp.items(), key=lambda kv: -kv[1]):
        print(f'  raw {v:.3f}   display {display(v,P):5.1f}   {nm}')
    vals = sorted(sc.values())
    print()
    print('version score percentiles (raw / display):')
    for q in (10, 25, 50, 75, 90, 99):
        v = vals[int(len(vals) * q / 100)]
        print(f'  p{q:<3} {v:.4f}   {display(v,P):5.1f}')
