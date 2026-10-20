#!/usr/bin/env python3
"""harbor_score_db.py - build & maintain the harbor benchmark score database.

This is the *seed data* builder for the harbor badcase analyzer skill.
It only reads public, read-only data from the harbor hub.

Subcommands
-----------
  ingest-leaderboard [--ref REF]   pull a leaderboard + resolve row -> job_id map
  ingest-job JOB_ID [JOB_ID ...]   pull the full per-trial rows of one or more jobs
  ingest-all                       ingest-leaderboard, then every job in the map
  fit                              Rasch (1PL) fit -> irt_params + task_difficulty
  status                           coverage self-check

Everything is idempotent: rows are upserted by (run_id, trial_id), so the DB can
be refreshed incrementally and every snapshot is also kept under <skill>/raw/hub/.

This tool ships inside the skill, so all paths are relative to the skill root:
  <skill>/tools/harbor_score_db.py     this file
  <skill>/raw/hub/                     raw json snapshots
  <skill>/assets/harbor_scores.db      the database

Run inside WSL with the proxy enabled, e.g.:
  source /mnt/d/wsl_wkp/proxy_set.sh
  python3 <skill>/tools/harbor_score_db.py ingest-all
"""

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(SKILL_DIR, "raw", "hub")
# The db lives inside the skill package so the skill is self-contained.
DB_PATH = os.path.join(SKILL_DIR, "assets", "harbor_scores.db")

DEFAULT_REF = "terminal-bench/terminal-bench-2-1/main"
HUB_BASE = "https://hub.harborframework.com"
JOB_TRIALS_LIMIT = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id           TEXT PRIMARY KEY,
  source           TEXT,
  job_id           TEXT,
  job_name         TEXT,
  benchmark        TEXT,
  dataset_ref      TEXT,
  agent_name       TEXT,
  agent_version    TEXT,
  model_name       TEXT,
  model_provider   TEXT,
  effort           TEXT,
  config_key       TEXT,
  n_trials         INTEGER,
  mean_reward      REAL,
  fetched_at       TEXT,
  url              TEXT
);
CREATE TABLE IF NOT EXISTS trials(
  run_id         TEXT,
  trial_id       TEXT,
  trial_name     TEXT,
  task_name      TEXT,
  reward         REAL,
  status         TEXT,
  exception_type TEXT,
  is_scored      INTEGER,
  attempt        INTEGER,
  cost_usd       REAL,
  input_tokens   INTEGER,
  output_tokens  INTEGER,
  cache_tokens   INTEGER,
  duration_sec   REAL,
  started_at     TEXT,
  finished_at    TEXT,
  PRIMARY KEY(run_id, trial_id)
);
CREATE TABLE IF NOT EXISTS irt_params(
  benchmark    TEXT,
  config_key   TEXT,
  theta        REAL,
  delta_agent  REAL,
  n_trials     INTEGER,
  fitted_at    TEXT,
  PRIMARY KEY(benchmark, config_key)
);
CREATE TABLE IF NOT EXISTS task_difficulty(
  benchmark  TEXT,
  task_name  TEXT,
  b          REAL,
  se         REAL,
  n_obs      INTEGER,
  fitted_at  TEXT,
  PRIMARY KEY(benchmark, task_name)
);
CREATE TABLE IF NOT EXISTS leaderboard(
  source      TEXT,
  benchmark   TEXT,
  config_key  TEXT,
  agent       TEXT,
  model       TEXT,
  effort      TEXT,
  score       REAL,
  rank        INTEGER,
  n_trials    INTEGER,
  row_id      TEXT,
  snapshot_at TEXT,
  url         TEXT,
  PRIMARY KEY(source, benchmark, config_key)
);
CREATE INDEX IF NOT EXISTS idx_trials_task ON trials(run_id, task_name);
CREATE INDEX IF NOT EXISTS idx_runs_cfg    ON runs(benchmark, config_key);
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def harbor_bin():
    p = os.path.expanduser("~/.local/bin/harbor")
    if os.path.exists(p):
        return p
    w = shutil.which("harbor")
    if w:
        return w
    sys.exit("ERROR: harbor CLI not found (expected ~/.local/bin/harbor)")


def run_harbor(args):
    """Run the harbor CLI and return parsed JSON from stdout."""
    proc = subprocess.run([harbor_bin()] + args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "harbor %s failed (rc=%s)\n%s"
            % (" ".join(args), proc.returncode, proc.stderr[-2000:])
        )
    return proc.stdout


def load_json_text(text):
    """Parse JSON tolerating leading/trailing noise on stdout."""
    t = text.strip()
    for i, ch in enumerate(t):
        if ch in "{[":
            return json.loads(t[i:])
    raise ValueError("no JSON found in output")


def run_harbor_json(args):
    return load_json_text(run_harbor(args))


def unwrap(v):
    """config_values entries are JSON-quoted strings, e.g. '"max"'."""
    if isinstance(v, str):
        try:
            r = json.loads(v)
            return r if isinstance(r, str) else v
        except Exception:
            return v
    return v


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def duration_sec(started, finished):
    a, b = parse_ts(started), parse_ts(finished)
    if a and b:
        return (b - a).total_seconds()
    return None


def normalize_task(task_name):
    """'terminal-bench/regex-chess' -> 'regex-chess'"""
    if not task_name:
        return task_name
    return str(task_name).rsplit("/", 1)[-1]


def benchmark_from_ref(ref):
    """'terminal-bench/terminal-bench-2-1' -> 'terminal-bench-2-1'"""
    if not ref:
        return None
    return str(ref).rsplit("/", 1)[-1]


def config_key(agent, model, effort):
    return "%s|%s|%s" % (agent or "?", model or "?", effort or "")


def run_id_for(job_id, cfg_key):
    return "%s::%s" % (job_id, cfg_key)


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


# --------------------------------------------------------------------------- #
# ingest-leaderboard
# --------------------------------------------------------------------------- #
def cmd_ingest_leaderboard(args):
    ref = args.ref
    os.makedirs(RAW_DIR, exist_ok=True)
    log("[leaderboard] fetching", ref)
    lb = run_harbor_json(["hub", "leaderboard", "show", ref, "--json"])
    with open(os.path.join(RAW_DIR, "leaderboard.json"), "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=2)

    benchmark = benchmark_from_ref(ref.rsplit("/main", 1)[0])
    rows = lb.get("rows") or []
    mapping = []
    con = connect()
    ts = now_iso()
    for r in rows:
        md = r.get("metadata") or {}
        met = r.get("metrics") or {}
        agent = (md.get("agent_display") or {}).get("label")
        model = (md.get("model_display") or {}).get("label")
        effort = md.get("reasoning_effort")
        # resolve one real trial of this row -> its job_id
        job_id = None
        if r.get("id"):
            try:
                txt = run_harbor(
                    ["hub", "leaderboard", "row", "trial", "list",
                     r["id"], "--quiet", "--limit", "1"]
                )
                trial_id = next((ln.strip() for ln in txt.splitlines() if ln.strip()), None)
                if trial_id:
                    t = run_harbor_json(["hub", "trial", "show", trial_id, "--json"])
                    job_id = t.get("job_id")
            except Exception as e:  # noqa: BLE001
                log("[leaderboard] row %s job lookup failed: %s" % (r.get("id"), e))
        mapping.append({
            "rank": r.get("rank"), "row_id": r.get("id"), "job_id": job_id,
            "agent": agent, "model": model, "effort": effort,
        })
        acc = met.get("accuracy")
        score = None if acc is None else (acc / 100.0 if acc > 1.5 else acc)
        con.execute(
            "INSERT OR REPLACE INTO leaderboard"
            "(source,benchmark,config_key,agent,model,effort,score,rank,n_trials,row_id,snapshot_at,url)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hub", benchmark, config_key(agent, model, effort), agent, model, effort,
             score, r.get("rank"), met.get("n_trials"), r.get("id"), ts,
             "%s/datasets/%s?tab=leaderboard&leaderboard=main"
             % (HUB_BASE, ref.rsplit("/main", 1)[0])),
        )
        log("[leaderboard] #%s %s / %s / %s -> job %s"
            % (r.get("rank"), agent, model, effort, job_id))
    con.commit()
    con.close()

    with open(os.path.join(RAW_DIR, "jobmap.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    jobs = sorted({m["job_id"] for m in mapping if m["job_id"]})
    log("[leaderboard] %d rows -> %d distinct jobs" % (len(mapping), len(jobs)))
    return jobs


# --------------------------------------------------------------------------- #
# ingest-job
# --------------------------------------------------------------------------- #
def ingest_one_job(con, job_id):
    data = run_harbor_json(
        ["hub", "job", "trials", job_id, "--json", "--limit", str(JOB_TRIALS_LIMIT)]
    )
    items = data.get("items") or []
    with open(os.path.join(RAW_DIR, "%s.trials.json" % job_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    ts = now_iso()
    runs = {}
    for it in items:
        cv = it.get("config_values") or {}
        effort = unwrap(cv.get("agent.kwargs.reasoning_effort")) if isinstance(cv, dict) else None
        agent = it.get("agent_name")
        model = it.get("model_name")
        ref = it.get("source")
        benchmark = benchmark_from_ref(ref)
        ck = config_key(agent, model, effort)
        rid = run_id_for(job_id, ck)
        r = runs.setdefault(rid, {
            "source": "hub:%s" % ref if ref else "hub",
            "job_id": job_id, "job_name": it.get("job_name"), "benchmark": benchmark,
            "dataset_ref": ref, "agent_name": agent, "agent_version": it.get("agent_version"),
            "model_name": model, "model_provider": it.get("model_provider"),
            "effort": effort, "config_key": ck, "rewards": [],
        })
        if it.get("reward") is not None:
            r["rewards"].append(it["reward"])
        con.execute(
            "INSERT OR REPLACE INTO trials"
            "(run_id,trial_id,trial_name,task_name,reward,status,exception_type,is_scored,"
            " attempt,cost_usd,input_tokens,output_tokens,cache_tokens,duration_sec,started_at,finished_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, it.get("id"), it.get("name"), normalize_task(it.get("task_name")),
             it.get("reward"), it.get("status"), it.get("error_type"),
             1 if it.get("is_scored") else 0, it.get("attempt"), it.get("cost_usd"),
             it.get("input_tokens"), it.get("output_tokens"), it.get("cache_tokens"),
             duration_sec(it.get("started_at"), it.get("finished_at")),
             it.get("started_at"), it.get("finished_at")),
        )
    for rid, r in runs.items():
        rw = r.pop("rewards")
        con.execute(
            "INSERT OR REPLACE INTO runs"
            "(run_id,source,job_id,job_name,benchmark,dataset_ref,agent_name,agent_version,"
            " model_name,model_provider,effort,config_key,n_trials,mean_reward,fetched_at,url)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, r["source"], r["job_id"], r["job_name"], r["benchmark"], r["dataset_ref"],
             r["agent_name"], r["agent_version"], r["model_name"], r["model_provider"],
             r["effort"], r["config_key"], len(rw),
             (sum(rw) / len(rw)) if rw else None, ts,
             "%s/jobs/%s" % (HUB_BASE, job_id)),
        )
    con.commit()
    return len(items), len(runs)


def cmd_ingest_job(args):
    os.makedirs(RAW_DIR, exist_ok=True)
    con = connect()
    for jid in args.job_id:
        n, ncfg = ingest_one_job(con, jid)
        log("[job %s] %d trials, %d configs" % (jid, n, ncfg))
    con.close()


def cmd_ingest_all(args):
    jobs = cmd_ingest_leaderboard(args)
    if not jobs:
        sys.exit("ERROR: no jobs resolved from leaderboard")
    con = connect()
    for jid in jobs:
        n, ncfg = ingest_one_job(con, jid)
        log("[job %s] %d trials, %d configs" % (jid, n, ncfg))
    con.close()
    log("[ingest-all] done: %d jobs" % len(jobs))


# --------------------------------------------------------------------------- #
# fit
# --------------------------------------------------------------------------- #
def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def fit_rasch(cells, iters=300, tol=1e-8):
    """cells: {(config, task): (n, k)} -> (theta, b, se_b).

    1PL / Rasch JMLE with sum(theta)=0 centering after every sweep.
    """
    configs = sorted({c for c, _ in cells})
    tasks = sorted({t for _, t in cells})
    theta = {c: 0.0 for c in configs}
    b = {t: 0.0 for t in tasks}
    by_c, by_t = defaultdict(list), defaultdict(list)
    for (c, t), (n, k) in cells.items():
        by_c[c].append((t, n, k))
        by_t[t].append((c, n, k))

    def clamp(x):
        return max(-8.0, min(8.0, x))

    for _ in range(iters):
        maxd = 0.0
        for c in configs:
            g = h = 0.0
            for (t, n, k) in by_c[c]:
                p = _sigmoid(theta[c] - b[t])
                g += k - n * p
                h += n * p * (1.0 - p)
            if h > 1e-9:
                d = max(-1.0, min(1.0, g / h))
                theta[c] = clamp(theta[c] + d)
                maxd = max(maxd, abs(d))
        for t in tasks:
            g = h = 0.0
            for (c, n, k) in by_t[t]:
                p = _sigmoid(theta[c] - b[t])
                g += n * p - k
                h += n * p * (1.0 - p)
            if h > 1e-9:
                d = max(-1.0, min(1.0, g / h))
                b[t] = clamp(b[t] + d)
                maxd = max(maxd, abs(d))
        m = sum(theta.values()) / len(theta)
        for c in configs:
            theta[c] -= m
        for t in tasks:
            b[t] -= m
        if maxd < tol:
            break

    se_b = {}
    for t in tasks:
        h = sum(n * _sigmoid(theta[c] - b[t]) * (1 - _sigmoid(theta[c] - b[t]))
                for (c, n, k) in by_t[t])
        se_b[t] = (1.0 / math.sqrt(h)) if h > 1e-9 else None
    return theta, b, se_b


def decompose(theta_by_cfg, meta, iters=200):
    """theta ~= theta_model + delta_agent, least squares, sum(delta_agent)=0."""
    models = sorted({m for _a, m in meta.values()})
    agents = sorted({a for a, _m in meta.values()})
    tm = {m: 0.0 for m in models}
    da = {a: 0.0 for a in agents}
    items = [(theta_by_cfg[ck], meta[ck][0], meta[ck][1]) for ck in theta_by_cfg]
    for _ in range(iters):
        for m in models:
            vals = [th - da[a] for th, a, mm in items if mm == m]
            if vals:
                tm[m] = sum(vals) / len(vals)
        for a in agents:
            vals = [th - tm[m] for th, aa, m in items if aa == a]
            if vals:
                da[a] = sum(vals) / len(vals)
        shift = sum(da.values()) / len(da)
        for a in agents:
            da[a] -= shift
        for m in models:
            tm[m] += shift
    return da


def cmd_fit(args):
    con = connect()
    cur = con.cursor()
    rows = cur.execute(
        "SELECT r.benchmark, r.config_key, r.agent_name, r.model_name, t.task_name, t.reward"
        " FROM trials t JOIN runs r ON r.run_id = t.run_id"
        " WHERE t.reward IS NOT NULL AND t.is_scored = 1 AND r.benchmark IS NOT NULL"
    ).fetchall()
    bench_cells = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))  # bench -> cfg,task -> [n,k]
    bench_meta = defaultdict(dict)
    for bench, ck, agent, model, task, reward in rows:
        cell = bench_cells[bench][(ck, task)]
        cell[0] += 1
        cell[1] += float(reward)
        bench_meta[bench][ck] = (agent, model)

    ts = now_iso()
    for bench, cells in bench_cells.items():
        grid = {k: (v[0], v[1]) for k, v in cells.items()}
        theta, b, se_b = fit_rasch(grid)
        da = decompose(theta, bench_meta[bench])
        n_by_cfg = defaultdict(int)
        for (ck, _t), (n, _k) in grid.items():
            n_by_cfg[ck] += int(n)
        for ck, th in theta.items():
            cur.execute(
                "INSERT OR REPLACE INTO irt_params"
                "(benchmark,config_key,theta,delta_agent,n_trials,fitted_at) VALUES(?,?,?,?,?,?)",
                (bench, ck, th, da.get(bench_meta[bench][ck][0]), n_by_cfg[ck], ts),
            )
        n_by_task = defaultdict(int)
        for (_c, t), (n, _k) in grid.items():
            n_by_task[t] += int(n)
        for t, bb in b.items():
            cur.execute(
                "INSERT OR REPLACE INTO task_difficulty"
                "(benchmark,task_name,b,se,n_obs,fitted_at) VALUES(?,?,?,?,?,?)",
                (bench, t, bb, se_b.get(t), n_by_task[t], ts),
            )
        log("[fit %s] %d configs, %d tasks, %d cells"
            % (bench, len(theta), len(b), len(grid)))
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def cmd_status(args):
    con = connect()
    cur = con.cursor()
    print("DB:", DB_PATH)
    n = cur.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print("runs        :", n)
    print("trials      :", cur.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
    print("leaderboard :", cur.execute("SELECT COUNT(*) FROM leaderboard").fetchone()[0])
    print("irt_params  :", cur.execute("SELECT COUNT(*) FROM irt_params").fetchone()[0])
    print("difficulty  :", cur.execute("SELECT COUNT(*) FROM task_difficulty").fetchone()[0])
    print()
    for (bench,) in cur.execute("SELECT DISTINCT benchmark FROM runs WHERE benchmark IS NOT NULL"):
        print("== %s ==" % bench)
        for row in cur.execute(
            "SELECT config_key, agent_name, model_name, effort, n_trials,"
            " ROUND(mean_reward,4) FROM runs WHERE benchmark=? ORDER BY mean_reward DESC",
            (bench,),
        ):
            print("   %-42s n=%-5s mean=%s" % ("%s / %s / %s" % (row[1], row[2], row[3]),
                                              row[4], row[5]))
        print("   -- theta --")
        for row in cur.execute(
            "SELECT config_key, ROUND(theta,3), ROUND(delta_agent,3) FROM irt_params"
            " WHERE benchmark=? ORDER BY theta DESC", (bench,),
        ):
            print("   %-42s theta=%-7s delta_agent=%s" % (row[0], row[1], row[2]))
        print("   -- tasks --")
        for row in cur.execute(
            "SELECT COUNT(*),"
            " SUM(CASE WHEN n_obs>=3 THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN n_obs>=5 THEN 1 ELSE 0 END)"
            " FROM task_difficulty WHERE benchmark=?", (bench,),
        ):
            print("   tasks=%s  n_obs>=3: %s  n_obs>=5: %s" % row)
        thin = cur.execute(
            "SELECT task_name, n_obs FROM task_difficulty WHERE benchmark=? AND n_obs<3"
            " ORDER BY n_obs", (bench,),
        ).fetchall()
        if thin:
            print("   thin tasks (<3 obs):", ", ".join("%s(%s)" % t for t in thin))
        print()
    con.close()


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest-leaderboard")
    p.add_argument("--ref", default=DEFAULT_REF)
    p.set_defaults(func=cmd_ingest_leaderboard)

    p = sub.add_parser("ingest-job")
    p.add_argument("job_id", nargs="+")
    p.set_defaults(func=cmd_ingest_job)

    p = sub.add_parser("ingest-all")
    p.add_argument("--ref", default=DEFAULT_REF)
    p.set_defaults(func=cmd_ingest_all)

    sub.add_parser("fit").set_defaults(func=cmd_fit)
    sub.add_parser("status").set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
