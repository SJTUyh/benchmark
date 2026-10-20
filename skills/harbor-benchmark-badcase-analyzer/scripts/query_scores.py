#!/usr/bin/env python3
"""Stage 2 -- query the harbor score seed database.

Read-only access to the seed database (bundled as `assets/harbor_scores.db`,
built by the sibling `tools/harbor_score_db.py`).
Every function here is importable (used by `detect_badcases.py`); the CLI wrapper
just prints JSON so the values can be inspected by hand or piped elsewhere.

Units
-----
* config_key  = "<agent>|<model>|<effort>"          (see tools/harbor_score_db.py)
* mean_reward = trial-level reward averaged over a run (0..1)
* theta       = Rasch/1PL ability of a config       (fit, per benchmark)
* b           = Rasch/1PL difficulty of a task      (fit, per benchmark)
* p_hat       = estimated probability that the config passes the task

Subcommands
-----------
  exact       Beta-Binomial pass rate of (task, config) from raw trials
  difficulty  task difficulty b / se / n_obs
  theta       config ability theta / delta_agent (+ run mean_reward)
  leaderboard official leaderboard snapshot rows
  neighbors   T3: anchor a theta from a score, take the k nearest configs,
              and (optionally) vote their pass rate for one task
  snapshot    high level coverage of the database

Usage
-----
  python3 query_scores.py <subcommand> [options]
  python3 query_scores.py exact --task filter-js-from-html --agent claude-code --model deepseek-v4-flash

DB discovery: --db, else $HARBOR_SCORE_DB, else the copy bundled with this
skill (`<skill>/assets/harbor_scores.db`), else the nearest `data/harbor_scores.db`
walking up from this file / the current directory (legacy repo layout).
"""

import argparse
import json
import math
import os
import sqlite3
import sys

DEFAULT_BENCHMARK = "terminal-bench-2-1"
EPS = 1e-3  # weight damping for |theta difference|


# --------------------------------------------------------------------------- #
# db discovery / connection
# --------------------------------------------------------------------------- #
def find_db(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("HARBOR_SCORE_DB")
    if env:
        return env
    # db bundled inside this skill (self-contained / portable)
    bundled = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "harbor_scores.db")
    if os.path.exists(bundled):
        return bundled
    seen = set()
    for start in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        d = os.path.abspath(start)
        for _ in range(10):
            if d in seen:
                break
            seen.add(d)
            cand = os.path.join(d, "data", "harbor_scores.db")
            if os.path.exists(cand):
                return cand
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    sys.exit("ERROR: harbor_scores.db not found; pass --db or set $HARBOR_SCORE_DB")


def connect(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# core math
# --------------------------------------------------------------------------- #
def beta_posterior(k, n, alpha=1.0, beta=1.0):
    """Beta-Binomial shrunk pass rate.  k passes out of n trials."""
    return (k + alpha) / (n + alpha + beta)


def _sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def config_table(con, benchmark):
    """Per-config aggregate: trials-weighted mean_reward joined with fit theta."""
    cur = con.execute(
        """
        SELECT r.config_key                        AS config_key,
               MAX(r.agent_name)                   AS agent,
               MAX(r.model_name)                   AS model,
               MAX(r.effort)                       AS effort,
               SUM(r.n_trials)                     AS n_trials,
               SUM(r.mean_reward * r.n_trials) / SUM(r.n_trials) AS mean_reward
        FROM runs r
        WHERE r.benchmark = ? AND r.mean_reward IS NOT NULL
        GROUP BY r.config_key
        """,
        (benchmark,),
    )
    cfgs = {r["config_key"]: r for r in _rows(cur)}
    for p in _rows(con.execute(
            "SELECT config_key, theta, delta_agent FROM irt_params WHERE benchmark = ?",
            (benchmark,))):
        if p["config_key"] in cfgs:
            cfgs[p["config_key"]]["theta"] = p["theta"]
            cfgs[p["config_key"]]["delta_agent"] = p["delta_agent"]
    return sorted(cfgs.values(), key=lambda c: -(c.get("mean_reward") or 0.0))


def q_exact(con, benchmark, task, agent=None, model=None, effort=None,
            alpha=1.0, beta=1.0, min_n=3):
    """T1: empirical (shrunk) pass rate of a task for matching configs."""
    sql = """
        SELECT r.config_key, r.agent_name, r.model_name, r.effort,
               COUNT(*)                                   AS n,
               SUM(CASE WHEN t.reward >= 1.0 THEN 1 ELSE 0 END) AS k
        FROM trials t JOIN runs r ON r.run_id = t.run_id
        WHERE r.benchmark = ? AND t.task_name = ? AND t.reward IS NOT NULL
    """
    params = [benchmark, task]
    if agent:
        sql += " AND r.agent_name = ?"
        params.append(agent)
    if model:
        sql += " AND r.model_name = ?"
        params.append(model)
    if effort is not None:
        sql += " AND r.effort = ?"
        params.append(effort)
    sql += " GROUP BY r.config_key"

    out = []
    for row in _rows(con.execute(sql, params)):
        row["p_hat"] = beta_posterior(row["k"], row["n"], alpha, beta)
        row["usable"] = row["n"] >= min_n
        out.append(row)
    out.sort(key=lambda r: -r["p_hat"])
    return out


def q_difficulty(con, benchmark, task=None):
    if task:
        rows = _rows(con.execute(
            "SELECT * FROM task_difficulty WHERE benchmark = ? AND task_name = ?",
            (benchmark, task)))
    else:
        rows = _rows(con.execute(
            "SELECT * FROM task_difficulty WHERE benchmark = ? ORDER BY b DESC",
            (benchmark,)))
    return rows


def q_theta(con, benchmark, config_key=None, agent=None, model=None, effort=None):
    cfgs = config_table(con, benchmark)
    if config_key:
        cfgs = [c for c in cfgs if c["config_key"] == config_key]
    if agent:
        cfgs = [c for c in cfgs if c["agent"] == agent]
    if model:
        cfgs = [c for c in cfgs if c["model"] == model]
    if effort is not None:
        cfgs = [c for c in cfgs if c["effort"] == effort]
    return cfgs


def q_leaderboard(con, benchmark, source=None):
    sql = "SELECT * FROM leaderboard WHERE benchmark = ?"
    params = [benchmark]
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY rank"
    return _rows(con.execute(sql, params))


def _interp_theta(pairs, score):
    """Linear interpolation (and endpoint extrapolation) of theta(score).

    pairs: list of (mean_reward, theta) sorted by mean_reward ascending.
    """
    pts = sorted(pairs)
    if len(pts) < 2:
        return pts[0][1] if pts else None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if score <= xs[0]:
        i = 0
    elif score >= xs[-1]:
        i = len(pts) - 2
    else:
        i = max(j for j in range(len(pts) - 1) if xs[j] <= score)
    x0, x1 = xs[i], xs[i + 1]
    y0, y1 = ys[i], ys[i + 1]
    if x1 == x0:
        return (y0 + y1) / 2.0
    return y0 + (y1 - y0) * (score - x0) / (x1 - x0)


def q_neighbors(con, benchmark, anchor_score, k=3, task=None,
                alpha=1.0, beta=1.0, eps=EPS, min_k=3):
    """T3 neighbour vote.

    Anchor a theta by interpolating the (mean_reward -> theta) curve at
    `anchor_score`, then take the `k` configs with the smallest |theta diff|,
    weight them as w = 1/(eps + diff), and (if `task` is given) vote their
    Beta-shrunk pass rate on that task.
    """
    cfgs = [c for c in config_table(con, benchmark) if c.get("theta") is not None]
    pairs = [(c["mean_reward"], c["theta"]) for c in cfgs if c["mean_reward"] is not None]
    if len(pairs) < 2:
        return {"ok": False, "reason": "not enough fitted configs to anchor",
                "n_configs": len(pairs)}

    theta_anchor = _interp_theta(pairs, anchor_score)

    ranked = sorted(cfgs, key=lambda c: abs(c["theta"] - theta_anchor))
    picked = ranked[:max(1, k)]
    weights, nn = [], []
    for c in picked:
        diff = abs(c["theta"] - theta_anchor)
        w = 1.0 / (eps + diff)
        weights.append(w)
        entry = {
            "config_key": c["config_key"], "agent": c["agent"], "model": c["model"],
            "effort": c["effort"], "theta": round(c["theta"], 4),
            "mean_reward": round(c["mean_reward"], 4),
            "theta_diff": round(diff, 4), "weight": w,
        }
        if task:
            ex = q_exact(con, benchmark, task, agent=c["agent"], model=c["model"],
                         effort=c["effort"], alpha=alpha, beta=beta)
            n = sum(r["n"] for r in ex)
            kk = sum(r["k"] for r in ex)
            entry.update({"task_n": n, "task_k": kk,
                          "task_p_hat": beta_posterior(kk, n, alpha, beta)})
        nn.append(entry)

    result = {
        "theta_anchor": round(theta_anchor, 4),
        "anchor_score": anchor_score,
        "n_configs_considered": len(cfgs),
        "k_requested": k,
        "neighbors": nn,
        "ok": len(nn) >= min_k,
    }
    if not result["ok"]:
        result["reason"] = "only %d neighbours (< %d required)" % (len(nn), min_k)
    if task and nn:
        tot_w = sum(e["weight"] for e in nn)
        p = sum(e["weight"] * e["task_p_hat"] for e in nn) / tot_w
        result["task"] = task
        result["p_hat"] = round(p, 4)
        result["n_neighbors_with_task_data"] = sum(1 for e in nn if e["task_n"] > 0)
        result["confidence"] = "T3/low"
    return result


def q_snapshot(con):
    out = {"benchmarks": [r["benchmark"] for r in _rows(con.execute(
        "SELECT DISTINCT benchmark FROM runs ORDER BY benchmark"))]}
    for b in out["benchmarks"]:
        out.setdefault("tables", {})[b] = {
            "runs": con.execute("SELECT COUNT(*) FROM runs WHERE benchmark=?", (b,)).fetchone()[0],
            "trials": con.execute(
                "SELECT COUNT(*) FROM trials t JOIN runs r ON r.run_id=t.run_id WHERE r.benchmark=?",
                (b,)).fetchone()[0],
            "fitted_configs": con.execute(
                "SELECT COUNT(*) FROM irt_params WHERE benchmark=?", (b,)).fetchone()[0],
            "tasks": con.execute(
                "SELECT COUNT(*) FROM task_difficulty WHERE benchmark=?", (b,)).fetchone()[0],
            "leaderboard_rows": con.execute(
                "SELECT COUNT(*) FROM leaderboard WHERE benchmark=?", (b,)).fetchone()[0],
        }
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="path to harbor_scores.db")
    ap.add_argument("--benchmark", default=DEFAULT_BENCHMARK)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("exact")
    p.add_argument("--task", required=True)
    p.add_argument("--agent")
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--alpha", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=1.0)
    p.add_argument("--min-n", type=int, default=3)

    p = sub.add_parser("difficulty")
    p.add_argument("--task")

    p = sub.add_parser("theta")
    p.add_argument("--config-key")
    p.add_argument("--agent")
    p.add_argument("--model")
    p.add_argument("--effort")

    p = sub.add_parser("leaderboard")
    p.add_argument("--source")

    p = sub.add_parser("neighbors")
    p.add_argument("--anchor-score", type=float, required=True)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--task")
    p.add_argument("--alpha", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=1.0)

    sub.add_parser("snapshot")

    args = ap.parse_args()
    con = connect(find_db(args.db))
    b = args.benchmark

    if args.cmd == "exact":
        out = q_exact(con, b, args.task, args.agent, args.model, args.effort,
                      args.alpha, args.beta, args.min_n)
    elif args.cmd == "difficulty":
        out = q_difficulty(con, b, args.task)
    elif args.cmd == "theta":
        out = q_theta(con, b, args.config_key, args.agent, args.model, args.effort)
    elif args.cmd == "leaderboard":
        out = q_leaderboard(con, b, args.source)
    elif args.cmd == "neighbors":
        out = q_neighbors(con, b, args.anchor_score, args.k, args.task,
                          args.alpha, args.beta)
    else:
        out = q_snapshot(con)

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
