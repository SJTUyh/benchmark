#!/usr/bin/env python3
"""Stage 1 -- profile a harbor job results directory.

Reads a harbor job output directory (read-only) and emits a normalized JSON
profile with two layers:

  * fixed layer  -- harbor standard artifacts: job level `config.json`,
    `lock.json`, `result.json`, `job.log`; trial level `config.json`,
    `result.json`, `trial.log`, `exception.txt`, `verifier/ctrf.json`,
    `verifier/reward.txt`, `verifier/test-stdout.txt`,
    `artifacts/manifest.json`, `agent/trajectory.json`.
  * variation layer -- agent-specific artifacts.  These are *discovered*, never
    hard-coded: each trial's `agent/` directory is enumerated and matched
    against the adapter registry below.  Anything not covered by a registered
    adapter is reported and flagged `needs_user_confirmation` instead of being
    guessed (see the approval rules in SKILL.md).

Usage
-----
  python3 profile_job.py <job_dir> [--out FILE] [--summary]
  python3 profile_job.py <job_dir> --json

This script never writes inside <job_dir>.
"""

import argparse
import json
import os
import sys

# --------------------------------------------------------------------------- #
# fixed layer: harbor standard artifacts (see references/result_files.md)
# --------------------------------------------------------------------------- #
JOB_FIXED_FILES = {
    "config.json": "job config: agents / datasets / timeouts / verifier env",
    "lock.json": "resolved job lock: harbor version, retry policy, per-task digest",
    "result.json": "job result: per-eval metrics, reward_stats, exception_stats, tokens",
    "job.log": "job log: image build / deps install anomalies",
}

TRIAL_FIXED_FILES = {
    "config.json": "trial config: agent, env, verifier, timeouts",
    "result.json": "trial result: reward, exception_info, stage timings, tokens",
    "trial.log": "trial log: deps bundle, agent launch command, runtime warnings",
    "exception.txt": "full exception traceback",
    "verifier/ctrf.json": "ctrf test report: per-test pass/fail and trace",
    "verifier/reward.txt": "authoritative reward file",
    "verifier/test-stdout.txt": "verifier stdout/stderr",
    "artifacts/manifest.json": "artifact collection status",
    "agent/trajectory.json": "ATIF v1.7 trajectory (agent agnostic)",
}

# --------------------------------------------------------------------------- #
# variation layer: agent adapter registry.
#
# A registry entry lists the *relative glob patterns* a known agent writes
# inside `agent/`.  Discovery is: read agent name -> enumerate `agent/` ->
# match patterns.  Agents absent from this registry are NOT parsed; the skill
# must stop and ask the user before interpreting them.
# --------------------------------------------------------------------------- #
AGENT_ADAPTERS = {
    "claude-code": {
        "agent_names": ["claude-code", "claude_code", "claude"],
        "patterns": [
            {"glob": "agent/claude-code.txt", "kind": "claude-code stream-json raw log"},
            {"glob": "agent/sessions/tasks/*.json", "kind": "agent self-managed todo list"},
            {"glob": "agent/sessions/*.json", "kind": "agent session transcript"},
        ],
    },
}

SECRET_HINTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def infer_benchmark(dataset_path):
    """'terminal-bench-2-1/tasks' -> 'terminal-bench-2-1'; 'nl2repobench' -> itself."""
    p = (dataset_path or "").rstrip("/")
    if p.endswith("/tasks"):
        p = p[: -len("/tasks")]
    return p.rsplit("/", 1)[-1] if p else None


def read_json(path):
    """Return parsed JSON, or None if missing, or {'_parse_error': ...}."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"_parse_error": "%s: %s" % (type(e).__name__, e)}


def mask(value):
    if not isinstance(value, str) or len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-2:]


def mask_env(env):
    out = {}
    for k, v in (env or {}).items():
        out[k] = mask(v) if any(h in k.upper() for h in SECRET_HINTS) else v
    return out


def glob_match(rel_path, pattern):
    """Minimal glob supporting '**'-less patterns with a single '*' per segment."""
    p_seg = pattern.split("/")
    r_seg = rel_path.split("/")
    if len(p_seg) != len(r_seg):
        return False
    for p, r in zip(p_seg, r_seg):
        if p == "*":
            continue
        if "*" in p:
            head, tail = p.split("*", 1)
            if not (r.startswith(head) and r.endswith(tail)):
                return False
        elif p != r:
            return False
    return True


# --------------------------------------------------------------------------- #
# job level
# --------------------------------------------------------------------------- #
def harvest_job_result(job_result):
    """reward_stats / exception_stats live per eval in the job result.json."""
    rewards, exceptions, evals = {}, {}, {}
    stats = (job_result or {}).get("stats") or {}
    for key, ev in (stats.get("evals") or {}).items():
        evals[key] = {
            "n_trials": ev.get("n_trials"),
            "n_errors": ev.get("n_errors"),
            "metrics": ev.get("metrics"),
            "pass_at_k": ev.get("pass_at_k"),
        }
        rs = ((ev.get("reward_stats") or {}).get("reward")) or {}
        for val, names in rs.items():
            try:
                rv = float(val)
            except (TypeError, ValueError):
                rv = None
            for n in names:
                rewards[n] = rv
        for exc, names in (ev.get("exception_stats") or {}).items():
            for n in names:
                exceptions[n] = exc
    return rewards, exceptions, evals


def profile_job_config(cfg):
    agents = []
    for a in (cfg or {}).get("agents") or []:
        env = a.get("env") or {}
        agents.append({
            "name": a.get("name"),
            "model_name": a.get("model_name"),
            "kwargs": a.get("kwargs") or {},
            "disallowed_tools": (a.get("kwargs") or {}).get("disallowed_tools"),
            "effort": env.get("CLAUDE_CODE_EFFORT_LEVEL") or (a.get("kwargs") or {}).get("effort"),
            "deps_path": a.get("deps_path"),
            "extra_allowed_hosts": a.get("extra_allowed_hosts") or [],
            "env": mask_env(env),
            "env_keys": sorted(env.keys()),
        })
    datasets = []
    for d in (cfg or {}).get("datasets") or []:
        path = (d.get("path") or "").rstrip("/")
        datasets.append({"path": path, "name": infer_benchmark(path)})
    return {
        "job_name": (cfg or {}).get("job_name"),
        "timeout_multiplier": (cfg or {}).get("timeout_multiplier"),
        "n_concurrent_trials": (cfg or {}).get("n_concurrent_trials"),
        "environment": (cfg or {}).get("environment"),
        "verifier_env_keys": sorted(((cfg or {}).get("verifier") or {}).get("env", {}).keys()),
        "agents": agents,
        "datasets": datasets,
    }


def profile_lock(lock):
    if not isinstance(lock, dict) or "_parse_error" in lock:
        return {"present": bool(lock)} if lock else None
    trials = lock.get("trials") or []
    return {
        "schema_version": lock.get("schema_version"),
        "created_at": lock.get("created_at"),
        "harbor": lock.get("harbor"),
        "n_concurrent_trials": lock.get("n_concurrent_trials"),
        "retry": lock.get("retry"),
        "n_trials": len(trials),
        "tasks": [{"name": (t.get("task") or {}).get("name"),
                   "digest": (t.get("task") or {}).get("digest")} for t in trials],
    }


def tail_lines(path, n=40):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception as e:  # noqa: BLE001
        return ["<read error: %s>" % e]
    return lines[-n:]


# --------------------------------------------------------------------------- #
# trial level
# --------------------------------------------------------------------------- #
def profile_trial(trial_dir, name, job_rewards, job_exceptions, agent_names):
    task = name.rsplit("__", 1)[0] if "__" in name else name
    info = {"trial_dir": name, "task_name": task, "files": {}, "warnings": []}

    for rel, _desc in TRIAL_FIXED_FILES.items():
        p = os.path.join(trial_dir, *rel.split("/"))
        if os.path.exists(p):
            info["files"][rel] = os.path.getsize(p)

    res = read_json(os.path.join(trial_dir, "result.json"))
    if isinstance(res, dict) and "_parse_error" not in res:
        info["trial_id"] = res.get("id")
        info["task_name_full"] = res.get("task_name")
        info["reward"] = (((res.get("verifier_result") or {}).get("rewards")) or {}).get("reward")
        info["reward_source"] = "trial.result.json"
        exc = res.get("exception_info") or None
        info["exception_type"] = (exc or {}).get("exception_type")
        info["exception_message"] = (exc or {}).get("exception_message")
        ai = res.get("agent_info") or {}
        info["agent_info"] = {
            "name": ai.get("name"), "version": ai.get("version"),
            "model": (ai.get("model_info") or {}).get("name"),
            "provider": (ai.get("model_info") or {}).get("provider"),
        }
        ar = res.get("agent_result") or {}
        info["agent_result"] = {k: ar.get(k) for k in
                                ("n_input_tokens", "n_cache_tokens",
                                 "n_output_tokens", "cost_usd")}
        info["stage_timings"] = {k: res.get(k) for k in
                                 ("started_at", "finished_at", "environment_setup",
                                  "agent_setup", "agent_execution", "verifier")}
    else:
        if res and "_parse_error" in res:
            info["warnings"].append("trial result.json unparsable: %s" % res["_parse_error"])
        info["reward"] = None

    # authoritative reward file
    reward_txt = os.path.join(trial_dir, "verifier", "reward.txt")
    if os.path.exists(reward_txt):
        try:
            with open(reward_txt, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read().strip()
            info["reward_txt"] = float(raw)
        except Exception:  # noqa: BLE001
            info["reward_txt"] = raw

    # ctrf test report
    ctrf = read_json(os.path.join(trial_dir, "verifier", "ctrf.json"))
    if isinstance(ctrf, dict) and "_parse_error" not in ctrf:
        results = ctrf.get("results") or {}
        summary = results.get("summary") or {}
        tests = results.get("tests") or []
        info["ctrf"] = {
            "tool": (results.get("tool") or {}).get("name"),
            "n_tests": len(tests),
            "summary": {k: summary.get(k) for k in
                        ("tests", "passed", "failed", "skipped", "pending", "other")},
            "failed_tests": [t.get("name") for t in tests if t.get("status") == "failed"],
        }

    # artifact collection manifest
    manifest = read_json(os.path.join(trial_dir, "artifacts", "manifest.json"))
    if isinstance(manifest, list):
        info["artifacts"] = [{"destination": m.get("destination"), "status": m.get("status")}
                             for m in manifest]

    # variation layer: enumerate agent/ and match adapters
    agent_dir = os.path.join(trial_dir, "agent")
    agent_files = []
    if os.path.isdir(agent_dir):
        for root, _dirs, files in os.walk(agent_dir):
            for fn in files:
                p = os.path.join(root, fn)
                agent_files.append(os.path.relpath(p, trial_dir).replace(os.sep, "/"))
    info["agent_files"] = sorted(agent_files)

    matched = {}
    for aname in agent_names:
        adapter = AGENT_ADAPTERS.get(aname)
        if not adapter:
            continue
        hits = []
        for pat in adapter["patterns"]:
            for af in info["agent_files"]:
                if glob_match(af, pat["glob"]):
                    hits.append({"file": af, "kind": pat["kind"],
                                 "bytes": os.path.getsize(
                                     os.path.join(trial_dir, *af.split("/")))})
        if hits:
            matched[aname] = hits
    info["agent_artifacts_matched"] = matched

    # agent-declared todo list (claude-code adapter)
    todos = []
    sess_dir = os.path.join(trial_dir, "agent", "sessions", "tasks")
    if os.path.isdir(sess_dir):
        for fn in sorted(os.listdir(sess_dir)):
            if fn.endswith(".json"):
                todos.append("agent/sessions/tasks/" + fn)
    if todos:
        info["agent_todo_files"] = todos

    # fall back to the job level result.json when a trial never wrote its own
    if info.get("reward") is None and name in job_rewards:
        info["reward"] = job_rewards[name]
        info["reward_source"] = "job.result.json"
    if not info.get("exception_type") and name in job_exceptions:
        info["exception_type"] = job_exceptions[name]
        info["exception_source"] = "job.result.json"

    info.setdefault("reward", None)
    info.setdefault("exception_type", None)
    return info


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def profile(job_dir):
    job_dir = os.path.abspath(job_dir)
    if not os.path.isdir(job_dir):
        sys.exit("ERROR: not a directory: %s" % job_dir)

    out = {"job_dir": job_dir, "job_name": os.path.basename(job_dir), "warnings": []}

    cfg = read_json(os.path.join(job_dir, "config.json"))
    out["config"] = profile_job_config(cfg if isinstance(cfg, dict) else {})
    out["lock"] = profile_lock(read_json(os.path.join(job_dir, "lock.json")))
    job_result = read_json(os.path.join(job_dir, "result.json"))
    if not isinstance(job_result, dict):
        job_result = {}
    job_rewards, job_exceptions, evals = harvest_job_result(job_result)
    out["result"] = {
        "id": job_result.get("id"),
        "n_total_trials": job_result.get("n_total_trials"),
        "started_at": job_result.get("started_at"),
        "finished_at": job_result.get("finished_at"),
        "stats": {k: (job_result.get("stats") or {}).get(k) for k in
                  ("n_completed_trials", "n_errored_trials", "n_running_trials",
                   "n_pending_trials", "n_cancelled_trials", "n_retries",
                   "n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")},
        "evals": evals,
    }
    out["job_log_tail"] = tail_lines(os.path.join(job_dir, "job.log"), 40)

    # benchmark inference from dataset path
    dsn = out["config"]["datasets"]
    out["benchmark"] = dsn[0]["name"] if dsn else None

    agent_names = [a["name"] for a in out["config"]["agents"] if a.get("name")]
    unknown = sorted(set(agent_names) - {n for k, v in AGENT_ADAPTERS.items()
                                         for n in v["agent_names"]})
    out["agent_artifact_protocol"] = {
        "agent_names": agent_names,
        "registered_agents": sorted(AGENT_ADAPTERS.keys()),
        "unknown_agents": unknown,
        "needs_user_confirmation": bool(unknown),
        "note": ("Agent(s) %s have no registered artifact adapter.  Do NOT guess "
                 "their artifact semantics; stop and ask the user before "
                 "interpreting them." % unknown) if unknown else
                "All agents have a registered artifact adapter.",
    }

    trials = []
    for name in sorted(os.listdir(job_dir)):
        d = os.path.join(job_dir, name)
        if os.path.isdir(d) and "__" in name:
            trials.append(profile_trial(d, name, job_rewards, job_exceptions, agent_names))
    out["trials"] = trials

    # coverage of agent artifacts
    n_no_agent_dir = sum(1 for t in trials if not t["agent_files"])
    if agent_names and n_no_agent_dir:
        out["warnings"].append(
            "%d/%d trials have an empty/missing agent/ directory"
            % (n_no_agent_dir, len(trials)))
    return out


def summarize(p):
    trials = p["trials"]
    rated = [t for t in trials if t.get("reward") is not None]
    n_pass = sum(1 for t in rated if t["reward"] >= 1.0)
    n_fail = sum(1 for t in rated if t["reward"] < 1.0)
    exc = {}
    for t in trials:
        if t.get("exception_type"):
            exc[t["exception_type"]] = exc.get(t["exception_type"], 0) + 1
    return {
        "job_name": p["job_name"],
        "benchmark": p["benchmark"],
        "agents": p["agent_artifact_protocol"]["agent_names"],
        "models": [a.get("model_name") for a in p["config"]["agents"]],
        "effort": [a.get("effort") for a in p["config"]["agents"]],
        "n_trials": len(trials),
        "n_scored": len(rated),
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_reward_missing": len(trials) - len(rated),
        "mean_reward": (sum(t["reward"] for t in rated) / len(rated)) if rated else None,
        "exceptions": exc,
        "n_with_ctrf": sum(1 for t in trials if "ctrf" in t),
        "n_with_trajectory": sum(1 for t in trials if "agent/trajectory.json" in t["files"]),
        "n_without_trial_result_json": sum(
            1 for t in trials if "result.json" not in t["files"]),
        "needs_user_confirmation": p["agent_artifact_protocol"]["needs_user_confirmation"],
        "warnings": p["warnings"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("--out", help="write the full profile JSON here")
    ap.add_argument("--json", action="store_true", help="print the full profile JSON")
    ap.add_argument("--summary", action="store_true", help="print a short summary only")
    args = ap.parse_args()

    p = profile(args.job_dir)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
        log("[profile] wrote %s" % args.out)
    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize(p), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
