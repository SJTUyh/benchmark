#!/usr/bin/env python3
"""Stage 3 -- read an ATIF trajectory without flooding the context.

`agent/trajectory.json` follows ATIF v1.7 (agent agnostic) and can be hundreds of
KB.  This script loads it in a separate process and prints *truncated* summaries
only -- never the whole file.  Every string it prints is capped by --chars.

ATIF v1.7 shape (the parts used here):
  {schema_version, session_id,
   agent{name, version, model_name, extra{cwds, git_branches}},
   steps[{step_id, timestamp, source, message, reasoning_content, model_name,
          tool_calls[{tool_call_id, function_name, arguments, extra{raw_arguments,
                      tool_use_name}}],
          observation{results[{source_call_id, content,
                      extra{tool_result_metadata{tool_use_result{stdout, stderr,
                      interrupted, isImage, noOutputExpected}}}}]},
          extra{is_sidechain}}]}

Usage
-----
  python3 read_trajectory.py <trajectory.json | trial_dir> --list   (default)
  python3 read_trajectory.py <...> --step N [--chars N]
  python3 read_trajectory.py <...> --errors
  python3 read_trajectory.py <...> --json [--out FILE]
"""

import argparse
import json
import os
import sys

DEFAULT_CHARS = 300


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def resolve_path(path):
    if os.path.isdir(path):
        path = os.path.join(path, "agent", "trajectory.json")
    return path


def truncate(value, n):
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if n is not None and len(s) > n:
        return s[:n] + "...[+%d chars]" % (len(s) - n)
    return s


def result_meta(r):
    return ((((r.get("extra") or {}).get("tool_result_metadata") or {})
             .get("tool_use_result")) or {})


def result_status(r):
    md = result_meta(r)
    if md.get("interrupted"):
        return "interrupted"
    if md.get("stderr"):
        return "stderr"
    if md.get("isImage"):
        return "image"
    if md.get("noOutputExpected"):
        return "noout"
    return "ok"


def step_summary(st, chars):
    tcs = st.get("tool_calls") or []
    results = ((st.get("observation") or {}).get("results")) or []
    return {
        "step_id": st.get("step_id"),
        "source": st.get("source"),
        "timestamp": st.get("timestamp"),
        "model_name": st.get("model_name"),
        "is_sidechain": (st.get("extra") or {}).get("is_sidechain"),
        "message": truncate(st.get("message"), chars),
        "has_reasoning": bool(st.get("reasoning_content")),
        "tool_calls": [{"name": tc.get("function_name"),
                        "id": tc.get("tool_call_id"),
                        "args": truncate(tc.get("arguments"), chars)}
                       for tc in tcs],
        "n_results": len(results),
        "result_status": [result_status(r) for r in results],
        "result_head": [truncate(result_meta(r).get("stdout") or r.get("content"), chars)
                        for r in results],
    }


def step_detail(st, chars):
    tcs = st.get("tool_calls") or []
    results = ((st.get("observation") or {}).get("results")) or []
    return {
        "step_id": st.get("step_id"),
        "source": st.get("source"),
        "timestamp": st.get("timestamp"),
        "model_name": st.get("model_name"),
        "message": truncate(st.get("message"), chars),
        "reasoning_content": truncate(st.get("reasoning_content"), chars),
        "tool_calls": [{
            "name": tc.get("function_name"),
            "id": tc.get("tool_call_id"),
            "arguments": truncate(tc.get("arguments"), chars),
            "raw_arguments": truncate((tc.get("extra") or {}).get("raw_arguments"), chars),
        } for tc in tcs],
        "observation": [{
            "source_call_id": r.get("source_call_id"),
            "status": result_status(r),
            "stdout": truncate(result_meta(r).get("stdout"), chars),
            "stderr": truncate(result_meta(r).get("stderr"), chars),
            "interrupted": result_meta(r).get("interrupted"),
            "content": truncate(r.get("content"), chars),
        } for r in results],
    }


def is_error_step(st):
    """A step worth flagging: a tool call whose result errored / was interrupted,
    or a tool call that produced no result at all."""
    tcs = st.get("tool_calls") or []
    results = ((st.get("observation") or {}).get("results")) or []
    for r in results:
        if result_status(r) in ("stderr", "interrupted"):
            return True
    if tcs and not results:
        return True
    return False


def load(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def extract(traj, chars):
    steps = traj.get("steps") or []
    agent = traj.get("agent") or {}
    err = [i for i, s in enumerate(steps) if is_error_step(s)]
    tool_names = {}
    for s in steps:
        for tc in s.get("tool_calls") or []:
            n = tc.get("function_name")
            tool_names[n] = tool_names.get(n, 0) + 1
    return {
        "schema_version": traj.get("schema_version"),
        "session_id": traj.get("session_id"),
        "agent": {"name": agent.get("name"), "version": agent.get("version"),
                  "model_name": agent.get("model_name"),
                  "cwds": ((agent.get("extra") or {}).get("cwds")),
                  "git_branches": ((agent.get("extra") or {}).get("git_branches"))},
        "n_steps": len(steps),
        "first_timestamp": steps[0].get("timestamp") if steps else None,
        "last_timestamp": steps[-1].get("timestamp") if steps else None,
        "tool_call_counts": tool_names,
        "n_tool_calls": sum(tool_names.values()),
        "error_step_indexes": err,
        "steps": [step_summary(s, chars) for s in steps],
    }


def print_overview(ex):
    print("schema_version : %s" % ex["schema_version"])
    print("session_id     : %s" % ex["session_id"])
    print("agent          : %s %s  model=%s" % (
        ex["agent"]["name"], ex["agent"]["version"], ex["agent"]["model_name"]))
    if ex["agent"]["cwds"]:
        print("cwds           : %s" % truncate(ex["agent"]["cwds"], 200))
    if ex["agent"]["git_branches"]:
        print("git_branches   : %s" % truncate(ex["agent"]["git_branches"], 200))
    print("steps          : %d   tool_calls=%d   error_steps=%d"
          % (ex["n_steps"], ex["n_tool_calls"], len(ex["error_step_indexes"])))
    print("time           : %s -> %s" % (ex["first_timestamp"], ex["last_timestamp"]))
    print("tools          : %s" % json.dumps(ex["tool_call_counts"], ensure_ascii=False))
    print("")


def print_list(ex):
    print_overview(ex)
    for i, s in enumerate(ex["steps"]):
        tcs = ",".join(t["name"] or "?" for t in s["tool_calls"]) or "-"
        status = ",".join(s["result_status"]) or "-"
        msg = (s["message"] or "").replace("\n", " ")
        print("#%-3d %-5s %-8s tc=[%s] res=%s%s | %s" % (
            i, s["source"], s["result_status"] and status, tcs, s["n_results"],
            " *ERR*" if i in ex["error_step_indexes"] else "", msg))


def print_errors(ex):
    print_overview(ex)
    if not ex["error_step_indexes"]:
        print("(no error steps)")
        return
    for i in ex["error_step_indexes"]:
        s = ex["steps"][i]
        print("#%-3d %s tc=%s status=%s" % (
            i, s["source"], [t["name"] for t in s["tool_calls"]], s["result_status"]))
        for h in s["result_head"]:
            if h:
                print("      %s" % h.replace("\n", " ")[:400])
        print("")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="trajectory.json or a trial directory")
    ap.add_argument("--list", action="store_true", help="step list (default)")
    ap.add_argument("--step", type=int, help="print detailed step N (0-based)")
    ap.add_argument("--errors", action="store_true", help="only flag error steps")
    ap.add_argument("--chars", type=int, default=DEFAULT_CHARS,
                    help="max chars per printed string (default %d)" % DEFAULT_CHARS)
    ap.add_argument("--json", action="store_true", help="print extracted JSON")
    ap.add_argument("--out", help="write extracted JSON here")
    args = ap.parse_args()

    path = resolve_path(args.path)
    if not os.path.exists(path):
        sys.exit("ERROR: trajectory not found: %s" % path)
    traj = load(path)
    ex = extract(traj, args.chars)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ex, f, ensure_ascii=False, indent=2)
        log("[trajectory] wrote %s" % args.out)
    if args.json:
        print(json.dumps(ex, ensure_ascii=False, indent=2))
    elif args.step is not None:
        steps = traj.get("steps") or []
        if not (0 <= args.step < len(steps)):
            sys.exit("ERROR: step %d out of range (0..%d)" % (args.step, len(steps) - 1))
        print_overview(ex)
        print(json.dumps(step_detail(steps[args.step], args.chars),
                         ensure_ascii=False, indent=2))
    elif args.errors:
        print_errors(ex)
    else:
        print_list(ex)


if __name__ == "__main__":
    main()
