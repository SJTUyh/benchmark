#!/usr/bin/env python3
"""Stage 2 -- detect bad cases in a harbor job results directory.

Pipeline: `profile_job.profile()` -> per-trial signals -> statistical anomaly
scoring against the seed database (`query_scores`) -> ranked bad-case list.

Signals
-------
HARD (a trial is abnormal no matter how hard the task is):
  * `exception_type` non-empty                  (AgentTimeoutError, CancelledError, ...)
  * `reward` missing                            (no reward file / verdict)

SOFT (only *weight* the severity of a trial that already failed, reward < 1):
  * no `verifier/ctrf.json`
  * no agent output     (empty `agent/` directory)
  * `agent_result.cost_usd` is null
  `hard_multiplier = min(2.0, 1 + 0.25 * n_soft)`

NOTE / deviation from the plan: the plan listed "no ctrf.json", "zero tool calls"
and "cost null" as *hard* signals.  On the real round2 job most *passing* trials
also lack `verifier/` and `agent/` (e.g. build-cython-ext, bn-fit-modify have
reward=1.0 and no agent/), so those cannot stand as independent hard signals.
They are downgraded to severity weights here; see references/detection_algorithm.md.

STATISTICAL anomaly (failed but should not have):
  T1 exact  -- Beta-Binomial pass rate of (benchmark, task, target config), n>=min_n
  T2 IRT    -- p = sigmoid(theta_config - b_task)
  T3 vote   -- plan-mandated path for the target config: the target
               (claude-code | deepseek-v4-flash) is NOT on the leaderboard and has
               no fitted theta, so it is anchored by the job's own observed mean
               reward, and the k>=3 nearest configs vote.  Always labelled
               `T3/low` confidence and reported with the neighbours' per-task
               pass/fail facts as evidence.
  `is_badcase = has_hard_signal or (reward < 1 and p_hat >= p_threshold)`
  `severity    = (1 - p_hat) * hard_multiplier`   (hard-signal rows: p_hat=0)

Reverse check -- SUSPICIOUS PASS: reward >= 1 but no ctrf / ctrf.tests == 0 /
ctrf has failures / an exception is present.

If a failed, non-hard trial cannot be scored by T1/T2/T3 (e.g. < 3 neighbours or
no anchor), the run reports `status = NEED_USER_INPUT` and lists those trials:
this is out of the skill's scope and must be confirmed with the user.

Usage
-----
  python3 detect_badcases.py <job_dir> [--benchmark B] [--db FILE]
                             [--k 3] [--p-threshold 0.5] [--alpha 1] [--beta 1]
                             [--out report.json] [--json]
Read-only: never writes inside <job_dir>.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_job          # noqa: E402
import query_scores         # noqa: E402

DEFAULT_BENCHMARK = "terminal-bench-2-1"
SOFT_WEIGHT = 0.25
SOFT_CAP = 2.0
DEFAULT_P_THRESHOLD = 0.5


def _p_hat_of_trial(t, benchmark, con, alpha, beta, min_n, k, anchor_score):
    """Return (method, p_hat, confidence, extra) for one failed trial, or None."""
    task = t.get("task_name")
    agent = t.get("config_key")
    # target config of the run (single-agent jobs)
    tgt = t.get("_target") or {}
    # T1 -- exact empirical rate for the target config
    rows = query_scores.q_exact(con, benchmark, task,
                                agent=tgt.get("agent"), model=tgt.get("model"),
                                effort=tgt.get("effort"),
                                alpha=alpha, beta=beta, min_n=min_n)
    usable = [r for r in rows if r["usable"]]
    if usable:
        r = usable[0]
        return ("T1", r["p_hat"], "T1/exact",
                {"n": r["n"], "k": r["k"], "config_key": r["config_key"]})

    # T2 -- IRT with the target config's own theta (usually absent)
    theta_rows = query_scores.q_theta(con, benchmark,
                                      agent=tgt.get("agent"), model=tgt.get("model"),
                                      effort=tgt.get("effort"))
    theta_rows = [c for c in theta_rows if c.get("theta") is not None]
    diff = query_scores.q_difficulty(con, benchmark, task)
    if theta_rows and diff:
        b = diff[0]["b"]
        p = query_scores._sigmoid(theta_rows[0]["theta"] - b)
        return ("T2", p, "T2/irt",
                {"theta": theta_rows[0]["theta"], "b": b,
                 "config_key": theta_rows[0]["config_key"]})

    # T3 -- neighbour vote anchored on the job's observed mean reward
    nb = query_scores.q_neighbors(con, benchmark, anchor_score, k=k, task=task,
                                  alpha=alpha, beta=beta, min_k=3)
    if nb.get("ok") and "p_hat" in nb:
        return ("T3", nb["p_hat"], "T3/low",
                {"theta_anchor": nb["theta_anchor"], "anchor_score": anchor_score,
                 "n_neighbors": len(nb["neighbors"]),
                 "n_neighbors_with_task_data": nb.get("n_neighbors_with_task_data"),
                 "neighbors": nb["neighbors"]})
    return ("NONE", None, None, {"reason": nb.get("reason", "no anchor")})


def detect(job_dir, benchmark=None, db=None, k=3, alpha=1.0, beta=1.0,
           min_n=3, p_threshold=DEFAULT_P_THRESHOLD):
    p = profile_job.profile(job_dir)
    benchmark = benchmark or p.get("benchmark") or DEFAULT_BENCHMARK

    agents = p["config"]["agents"]
    target = {}
    if agents:
        target = {"agent": agents[0].get("name"),
                  "model": agents[0].get("model_name"),
                  "effort": agents[0].get("effort")}

    rated = [t for t in p["trials"] if t.get("reward") is not None]
    observed_mean = (sum(t["reward"] for t in rated) / len(rated)) if rated else None

    con = query_scores.connect(query_scores.find_db(db))

    out = {
        "job_dir": p["job_dir"],
        "job_name": p["job_name"],
        "benchmark": benchmark,
        "target_config": target,
        "observed_mean_reward": observed_mean,
        "params": {"k": k, "alpha": alpha, "beta": beta, "min_n": min_n,
                   "p_threshold": p_threshold},
        "n_trials": len(p["trials"]),
        "badcases": [],
        "suspicious_passes": [],
        "unscored_failures": [],
        "status": "OK",
    }

    for t in p["trials"]:
        t["_target"] = target
        reward = t.get("reward")
        exc = t.get("exception_type")

        hard = []
        if exc:
            hard.append("exception:%s" % exc)
        if reward is None:
            hard.append("reward-missing")

        soft = []
        if reward is not None and reward < 1.0:
            if "ctrf" not in t:
                soft.append("no-ctrf")
            if not t.get("agent_files"):
                soft.append("no-agent-output")
            if (t.get("agent_result") or {}).get("cost_usd") is None:
                soft.append("cost-null")
        multiplier = min(SOFT_CAP, 1.0 + SOFT_WEIGHT * len(soft))

        rec = {
            "trial": t["trial_dir"],
            "task": t.get("task_name"),
            "reward": reward,
            "hard_signals": hard,
            "soft_signals": soft,
            "severity_multiplier": round(multiplier, 3),
        }

        if hard:
            rec.update({"method": "hard", "p_hat": 0.0, "confidence": "hard",
                        "is_badcase": True,
                        "severity": round(1.0 * multiplier, 4)})
            rec["evidence"] = {"exception_type": exc,
                               "exception_message": t.get("exception_message"),
                               "reward_source": t.get("reward_source")}
            out["badcases"].append(rec)
            continue

        # ---- suspicious pass (reverse check) ----
        if reward is not None and reward >= 1.0:
            ctrf = t.get("ctrf")
            why = []
            if "ctrf" not in t:
                why.append("no-ctrf")
            elif ctrf.get("n_tests", 0) == 0:
                why.append("ctrf-zero-tests")
            elif ctrf.get("summary", {}).get("failed", 0):
                why.append("ctrf-has-failed")
            if exc:
                why.append("exception:%s" % exc)
            if why:
                out["suspicious_passes"].append({
                    "trial": t["trial_dir"], "task": t.get("task_name"),
                    "reward": reward, "reasons": why,
                    "ctrf": ctrf})
            continue

        # ---- failed, not hard -> statistical ----
        if reward is None:
            continue
        method, p_hat, conf, extra = _p_hat_of_trial(
            t, benchmark, con, alpha, beta, min_n, k, observed_mean)
        if method == "NONE":
            rec.update({"method": "NONE", "p_hat": None, "confidence": None,
                        "is_badcase": None, "severity": None, "extra": extra})
            out["unscored_failures"].append(rec)
            continue

        is_bad = p_hat >= p_threshold
        rec.update({"method": method, "p_hat": round(p_hat, 4), "confidence": conf,
                    "is_badcase": is_bad,
                    "severity": round((1.0 - p_hat) * multiplier, 4),
                    "evidence": extra})
        if is_bad:
            out["badcases"].append(rec)

    if out["unscored_failures"]:
        out["status"] = "NEED_USER_INPUT"
        out["note"] = ("%d failed trial(s) could not be scored by T1/T2/T3.  This is "
                       "outside the skill's scope -- stop and ask the user before "
                       "interpreting them." % len(out["unscored_failures"]))

    out["badcases"].sort(key=lambda r: -(r.get("severity") or 0.0))
    out["counts"] = {
        "badcases": len(out["badcases"]),
        "by_method": _tally(out["badcases"], "method"),
        "suspicious_passes": len(out["suspicious_passes"]),
        "unscored_failures": len(out["unscored_failures"]),
    }
    return out


def _tally(rows, key):
    d = {}
    for r in rows:
        d[r.get(key)] = d.get(r.get(key), 0) + 1
    return d


def summarize(rep):
    lines = [
        "job       : %s" % rep["job_name"],
        "benchmark : %s" % rep["benchmark"],
        "target    : %s" % rep["target_config"],
        "observed mean reward : %s" % (
            round(rep["observed_mean_reward"], 4)
            if rep["observed_mean_reward"] is not None else None),
        "trials    : %d" % rep["n_trials"],
        "status    : %s" % rep["status"],
        "counts    : %s" % json.dumps(rep["counts"], ensure_ascii=False),
        "",
        "top badcases (severity desc):",
    ]
    for r in rep["badcases"][:20]:
        lines.append("  %-42s reward=%-4s method=%-4s conf=%-9s p=%-6s sev=%s  %s"
                     % (r["trial"], r["reward"], r["method"], r["confidence"],
                        r["p_hat"], r["severity"],
                        ",".join(r["hard_signals"] + r["soft_signals"])))
    if rep["suspicious_passes"]:
        lines.append("")
        lines.append("suspicious passes:")
        for r in rep["suspicious_passes"][:20]:
            lines.append("  %-42s reward=%-4s %s"
                         % (r["trial"], r["reward"], ",".join(r["reasons"])))
    if rep["unscored_failures"]:
        lines.append("")
        lines.append("unscored failures (NEED_USER_INPUT):")
        for r in rep["unscored_failures"][:20]:
            lines.append("  %-42s reward=%-4s %s"
                         % (r["trial"], r["reward"],
                            (r.get("extra") or {}).get("reason")))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("--benchmark", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--beta", type=float, default=1.0)
    ap.add_argument("--min-n", type=int, default=3)
    ap.add_argument("--p-threshold", type=float, default=DEFAULT_P_THRESHOLD)
    ap.add_argument("--out", help="write the full report JSON here")
    ap.add_argument("--json", action="store_true", help="print the full report JSON")
    args = ap.parse_args()

    rep = detect(args.job_dir, args.benchmark, args.db, args.k,
                 args.alpha, args.beta, args.min_n, args.p_threshold)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("[detect] wrote %s" % args.out, file=sys.stderr)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(summarize(rep))


if __name__ == "__main__":
    main()
