#!/usr/bin/env python3
"""request_rate_search —— 为 ais_bench 性能测试任务寻优 request rate。

工具以子进程方式执行用户提供的 shell 脚本（内含 ais_bench 基准命令），每轮在命令
末尾追加 ``--request-rate <rate>`` 强制指定请求速率；运行期间实时监控 ais_bench
落盘的性能数据（``performances/<abbr>/tmp/tmp_*.jsonl`` + sqlite db），一旦不满足
"要求1" 立即中断本轮；每轮打印关键日志（请求数、Request Throughput），全部轮次
汇总到一个 CSV，最后打印 CSV 路径。

用法示例:
    python tools/request_rate_search/request_rate_search.py \\
        --script tools/request_rate_search/run_ais_bench.example.sh \\
        --rate-min 0.5 --rate-max 100 \\
        --ttft-threshold P90:2000 --ttft-threshold P95:2500 \\
        --tpot-threshold P90:50 \\
        --output-dir outputs/rate_search

依赖：Python3 标准库 + numpy。
"""

import argparse
import csv
import io
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import numpy as np

# ---------------------------------------------------------------------------
# 独立函数 1：要求1 —— 判断性能数据是否满足门限要求（可独立改动）
# ---------------------------------------------------------------------------

# 合法百分位：P1 ~ P99（与 ais_bench 的 PERCENTAGE_PATTERN 规则一致）
PERCENTILE_PATTERN = re.compile(r"^P(0*[1-9]\d{0,1})$")


def _limit_allowed_ratio(pxx: str, early_abort_ratio) -> float:
    """返回某个约束对允许的违反比例。

    pxx 为 "P90" 这类字符串；若指定了全局 early_abort_ratio 则直接使用，
    否则按百分位推导：(100 - PXX) / 100（P90 -> 0.1, P95 -> 0.05, P99 -> 0.01）。
    """
    if early_abort_ratio is not None:
        return float(early_abort_ratio)
    return (100 - int(pxx[1:])) / 100.0


def _check_metric(metric_name, samples, limits, expected_total, early_abort_ratio, finished):
    """校验单个指标的所有 (百分位, 门限) 约束对。

    Args:
        metric_name: 指标名（"TTFT"/"TPOT"，仅用于提示信息）
        samples: 已完成成功请求的指标样本列表（ms）
        limits: [(PXX, 门限ms), ...]
        expected_total: 预期总请求数（提前判定分母）；None 时回退为已完成样本数
        early_abort_ratio: 全局允许违反比例；None 时按各百分位推导
        finished: 是否所有请求已完成（最终判定）

    Returns:
        (通过: bool, 原因: str, 统计: {pxx: {threshold, current, violating, allowed}})
    """
    stats = {}
    for pxx, threshold in limits:
        current = float(np.percentile(samples, int(pxx[1:])))
        violating = int(sum(1 for v in samples if v > threshold))
        stats[pxx] = {
            "threshold": threshold,
            "current": current,
            "violating": violating,
            "allowed": _limit_allowed_ratio(pxx, early_abort_ratio),
        }

    if finished:
        # 最终判定：每个约束对均需 PXX(样本) <= 门限
        for pxx, threshold in limits:
            st = stats[pxx]
            if st["current"] > threshold:
                return False, (
                    f"最终判定不满足: {metric_name}[{pxx}]={st['current']:.2f}ms "
                    f"> 门限{threshold}ms"
                ), stats
        return True, f"{metric_name} 最终判定满足", stats

    # 运行中提前判定：违反比例 > 允许比例 即不满足
    denominator = expected_total if expected_total else len(samples)
    if denominator <= 0:
        denominator = len(samples)
    for pxx, threshold in limits:
        st = stats[pxx]
        ratio = st["violating"] / denominator
        if ratio > st["allowed"]:
            return False, (
                f"提前打断: {metric_name}[{pxx}] 违反比例{ratio:.2%} "
                f"(违反{st['violating']}/{denominator}) > 允许{st['allowed']:.2%}"
            ), stats
    return True, f"{metric_name} 运行中满足", stats


def requirement_1(ttft_ms, tpot_ms, ttft_limits, tpot_limits,
                  expected_total=None, early_abort_ratio=None,
                  min_samples=10, finished=False):
    """判断性能数据是否满足"要求1"（独立函数，可自由改动）。

    Args:
        ttft_ms: 已完成成功请求的 TTFT 样本列表（ms）
        tpot_ms: 已完成成功请求的 TPOT 样本列表（ms）
        ttft_limits: TTFT 约束对列表 [(PXX, 门限ms), ...]
        tpot_limits: TPOT 约束对列表 [(PXX, 门限ms), ...]
        expected_total: 预期总请求数（提前判定的分母）；None 时回退为已完成样本数
        early_abort_ratio: 全局允许违反比例；None 时按各百分位推导 (100-PXX)/100
        min_samples: 已完成成功样本数低于该值时不做提前判定（默认 10）
        finished: 是否所有请求已完成（True 走最终判定；False 走运行中提前判定）

    Returns:
        (通过: bool, 原因: str, 统计: {"TTFT": {...}, "TPOT": {...}})
    """
    reasons = []
    details = {}
    for metric_name, samples, limits in (
        ("TTFT", ttft_ms, ttft_limits),
        ("TPOT", tpot_ms, tpot_limits),
    ):
        if not finished and len(samples) < min_samples:
            reasons.append(f"{metric_name} 样本不足({len(samples)}<{min_samples})，暂不判定")
            details[metric_name] = {}
            continue
        if not samples:
            if finished:
                return False, f"最终判定不满足: {metric_name} 无有效样本", details
            reasons.append(f"{metric_name} 尚无样本")
            details[metric_name] = {}
            continue
        ok, reason, stats = _check_metric(
            metric_name, samples, limits, expected_total, early_abort_ratio, finished
        )
        details[metric_name] = stats
        if not ok:
            return False, reason, details
        reasons.append(reason)
    return True, " | ".join(reasons), details


# ---------------------------------------------------------------------------
# 独立函数 2：算法1 —— 根据历史轮次结果选择下一轮 request rate（可独立改动）
# ---------------------------------------------------------------------------

def algorithm_1(history, rate_min, rate_max, tol=0.05, descent_factor=0.5,
                rate_round=3):
    """根据历史轮次结果返回下一轮要尝试的 request rate；None 表示收敛或范围内无可行 rate。

    策略：从上限向下等比试探；出现通过点后，在通过点与上方最近失败点之间二分细化，
    直到区间宽度 <= tol * rate_max。

    Args:
        history: 历史轮次列表，每项 {"rate": float, "passed": bool, ...}
        rate_min / rate_max: 寻优范围
        tol: 收敛相对容差（相对 rate_max）
        descent_factor: 等比下降因子
        rate_round: 候选 rate 保留的小数位数

    Returns:
        下一轮 request rate，或 None（已收敛 / 范围无可行 rate）
    """
    tried = {round(h["rate"], rate_round) for h in history}
    passing = [h["rate"] for h in history if h.get("passed")]
    failing = [h["rate"] for h in history if not h.get("passed")]

    if not history:
        return round(rate_max, rate_round)

    if passing:
        best_passing = max(passing)
        upper_failures = [r for r in failing if r > best_passing]
        if not upper_failures:
            # rate_max 已通过，范围内已无更优（更高）的 rate
            return None
        failing_upper = min(upper_failures)
        if failing_upper - best_passing <= tol * rate_max:
            # 区间已小于容差，收敛
            return None
        candidate = round((best_passing + failing_upper) / 2.0, rate_round)
        # 去重：若该点已被尝试，向 best_passing 方向微移
        step = max((failing_upper - best_passing) * 0.01, 1e-6 * rate_max)
        guard = 0
        while candidate in tried and guard < 100:
            candidate = round(max(best_passing + step, candidate - step), rate_round)
            guard += 1
        return candidate

    # 一路失败：等比下降
    last_rate = history[-1]["rate"]
    if last_rate <= rate_min:
        return None
    candidate = max(rate_min, round(last_rate * descent_factor, rate_round))
    guard = 0
    while round(candidate, rate_round) in tried and guard < 100:
        if candidate <= rate_min:
            return None
        candidate = max(rate_min, round(candidate * descent_factor, rate_round))
        guard += 1
    return round(candidate, rate_round)


# ---------------------------------------------------------------------------
# 落盘性能数据解析（与 ais_bench 汇总器计算口径一致）
# ---------------------------------------------------------------------------

def load_numpy_from_db(db_path, ids=None):
    """从 ais_bench 的 sqlite db（表 numpy_store）加载 numpy 数组。

    Args:
        db_path: db 文件路径
        ids: 需要加载的行 id 集合；None 表示加载全部

    Returns:
        {row_id: np.ndarray}
    """
    out = {}
    if not db_path or not os.path.isfile(db_path):
        return out
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(db_path, timeout=30)
        except sqlite3.Error:
            return out
    try:
        cur = conn.cursor()
        if ids is None:
            cur.execute("SELECT id, arr_blob FROM numpy_store")
            rows = cur.fetchall()
        else:
            rows = []
            for rid in ids:
                cur.execute("SELECT id, arr_blob FROM numpy_store WHERE id=?", (rid,))
                row = cur.fetchone()
                if row is not None:
                    rows.append(row)
        for row_id, blob in rows:
            try:
                out[row_id] = np.load(io.BytesIO(blob), allow_pickle=False)
            except Exception:
                continue
    except sqlite3.Error:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def compute_ttft_tpot_from_time_points(time_points, output_tokens):
    """按 ais_bench 汇总口径计算单条请求的 TTFT / TPOT（返回 ms）。

    ttft = tp[1] - tp[0]; latency = tp[-1] - tp[0];
    tpot = (latency - ttft) / (output_tokens - 1)，output_tokens <= 1 时为 0。
    """
    if time_points is None or not isinstance(time_points, np.ndarray) or time_points.size <= 1:
        return None
    ttft = time_points[1] - time_points[0]
    latency = time_points[-1] - time_points[0]
    output_tokens = output_tokens or 0
    tpot = (latency - ttft) / (output_tokens - 1) if output_tokens > 1 else 0.0
    return ttft * 1000.0, tpot * 1000.0


class PerfMonitor:
    """增量扫描 ais_bench perf 模式流式落盘的 tmp 数据，累积 TTFT/TPOT 样本。

    数据源：<work>/performances/<abbr>/tmp/tmp_<uuid>.jsonl（每行一条请求），
    其中 numpy 数组（time_points）以 {"__db_ref__": rowid} 占位，实数据在同目录
    的 tmp_<uuid>.db（sqlite）中。
    """

    def __init__(self, monitor_dir):
        self.monitor_dir = monitor_dir
        self.seen_keys = set()   # 已成功解析的请求 key：(db_name, id, uuid)
        self.samples = []        # 累积样本 [(ttft_ms, tpot_ms), ...]
        self.array_cache = {}    # db 路径 -> {rowid: ndarray}

    def scan(self):
        """扫描一次，返回本轮新增样本列表 [(ttft_ms, tpot_ms), ...]。"""
        new_samples = []
        if not self.monitor_dir or not os.path.isdir(self.monitor_dir):
            return new_samples
        for fname in sorted(os.listdir(self.monitor_dir)):
            if not (fname.startswith("tmp_") and fname.endswith(".jsonl")):
                continue
            db_path = os.path.join(self.monitor_dir, fname[:-6] + ".db")
            fpath = os.path.join(self.monitor_dir, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue
            pending = []   # 本文件内尚未处理成功的请求
            needed = set() # 需要从 db 解析的 rowid
            for line in lines:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (obj.get("db_name"), obj.get("id"), obj.get("uuid"))
                if not obj.get("success") or key in self.seen_keys:
                    continue
                pending.append((key, obj))
                tp = obj.get("time_points")
                if isinstance(tp, dict) and "__db_ref__" in tp:
                    needed.add(tp["__db_ref__"])
            if not pending:
                continue
            arrays = self._get_arrays(db_path, needed)
            for key, obj in pending:
                tp = obj.get("time_points")
                if isinstance(tp, dict) and "__db_ref__" in tp:
                    tp = arrays.get(tp["__db_ref__"])
                elif isinstance(tp, list):
                    tp = np.array(tp, dtype=np.float64)
                ttft_tpot = compute_ttft_tpot_from_time_points(tp, obj.get("output_tokens"))
                if ttft_tpot is None:
                    continue  # 数据尚未落盘/提交，下一轮再试
                self.seen_keys.add(key)
                self.samples.append(ttft_tpot)
                new_samples.append(ttft_tpot)
        return new_samples

    def _get_arrays(self, db_path, ids):
        cached = self.array_cache.get(db_path, {})
        missing = [i for i in ids if i not in cached]
        if missing:
            loaded = load_numpy_from_db(db_path, set(missing))
            cached.update(loaded)
            self.array_cache[db_path] = cached
        return cached


def compute_samples_from_details(perf_dir):
    """从最终落盘的 <dataset>_details.jsonl + db_data/ 重新计算全部 TTFT/TPOT 样本。

    与汇总器（summarizer）读取的数据源一致，用于对正常完成轮次的最终判定。
    """
    raw = []          # 待处理请求对象列表
    db_needed = {}    # db_name -> {rowid, ...}
    if not perf_dir or not os.path.isdir(perf_dir):
        return []
    for fname in sorted(os.listdir(perf_dir)):
        if not fname.endswith("_details.jsonl"):
            continue
        try:
            with open(os.path.join(perf_dir, fname), encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not obj.get("success"):
                        continue
                    raw.append(obj)
                    tp = obj.get("time_points")
                    if isinstance(tp, dict) and "__db_ref__" in tp:
                        db_name = obj.get("db_name")
                        db_needed.setdefault(db_name, set()).add(tp["__db_ref__"])
        except OSError:
            continue

    arrays_by_db = {}
    db_data_dir = os.path.join(perf_dir, "db_data")
    for db_name, ids in db_needed.items():
        db_path = os.path.join(db_data_dir, db_name) if db_name else None
        arrays_by_db[db_name] = load_numpy_from_db(db_path, ids)

    samples = []
    for obj in raw:
        tp = obj.get("time_points")
        if isinstance(tp, dict) and "__db_ref__" in tp:
            tp = arrays_by_db.get(obj.get("db_name"), {}).get(tp["__db_ref__"])
        elif isinstance(tp, list):
            tp = np.array(tp, dtype=np.float64)
        ttft_tpot = compute_ttft_tpot_from_time_points(tp, obj.get("output_tokens"))
        if ttft_tpot is not None:
            samples.append(ttft_tpot)
    return samples


# ---------------------------------------------------------------------------
# 子进程输出收集与结果目录定位
# ---------------------------------------------------------------------------

class OutputCollector(threading.Thread):
    """读取 ais_bench 子进程输出：落盘到日志文件，并解析关键信息。"""

    def __init__(self, proc, log_path):
        super().__init__(daemon=True)
        self.proc = proc
        self.log_file = open(log_path, "w", encoding="utf-8")
        self.exp_dir = None      # "Current exp folder: <dir>"
        self.result_dir = None   # "Performance Result files located in <dir>"
        self.expected_total = None  # 看板进度 "N/M" 的分母
        self.lock = threading.Lock()

    def run(self):
        try:
            for raw in self.proc.stdout:
                line = raw.rstrip("\r\n")
                self.log_file.write(line + "\n")
                self.log_file.flush()
                self._parse(line)
        except Exception:
            pass
        finally:
            try:
                self.log_file.close()
            except Exception:
                pass

    def _parse(self, line):
        m = re.search(r"Current exp folder:\s*(\S+)", line)
        if m:
            with self.lock:
                self.exp_dir = m.group(1)
        m = re.search(r"Performance Result files located in\s+(\S+)", line)
        if m:
            with self.lock:
                self.result_dir = m.group(1).rstrip(".")
        m = re.search(r"(\d+)\s*/\s*(\d+)", line)
        if m:
            total = int(m.group(2))
            with self.lock:
                if self.expected_total is None or total > self.expected_total:
                    self.expected_total = total


def resolve_monitor_dir(collector, args):
    """根据收集到的日志/目录结构定位本轮落盘性能数据目录（performances/<abbr>/tmp）。"""
    base = None
    if collector.exp_dir:
        base = collector.exp_dir
    else:
        # 回退：扫描 --work-dir 下最新时间戳目录
        if os.path.isdir(args.work_dir):
            dirs = sorted(
                d for d in os.listdir(args.work_dir)
                if os.path.isdir(os.path.join(args.work_dir, d))
            )
            if dirs:
                base = os.path.join(args.work_dir, dirs[-1])
    if base is None:
        return None
    perf_root = os.path.join(base, "performances")
    if not os.path.isdir(perf_root):
        return None
    if args.model_abbr:
        abbr = args.model_abbr
    else:
        abbrs = [d for d in os.listdir(perf_root)
                 if os.path.isdir(os.path.join(perf_root, d))]
        if not abbrs:
            return None
        abbr = max(abbrs, key=lambda d: os.path.getmtime(os.path.join(perf_root, d)))
    monitor_dir = os.path.join(perf_root, abbr, "tmp")
    if not os.path.isdir(monitor_dir):
        return None
    return monitor_dir


def terminate_process(proc):
    """终止 ais_bench 子进程（含其子进程树）。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # Windows：杀掉整个进程树
        subprocess.run(["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------------
# 最终结果读取（JSON 公共指标 + CSV 各 PXX 列）
# ---------------------------------------------------------------------------

def _parse_ms_value(val_str):
    """把 "2006.0 ms" 之类的结果解析为 float（ms）；无法解析返回 None。"""
    if val_str is None:
        return None
    text = str(val_str).strip()
    text = text.replace("ms", "").replace("req/s", "").strip()
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def read_final_results(result_dir, ttft_limits, tpot_limits):
    """读取最终结果目录：JSON 公共指标（Request Throughput/Total/Success）+ CSV 各 PXX。"""
    res = {
        "throughput": None,
        "total_requests": None,
        "success_requests": None,
        "ttft_pxx": {},
        "tpot_pxx": {},
    }
    if not result_dir or not os.path.isdir(result_dir):
        return res

    def _get_common(data, key):
        val = data.get(key)
        if isinstance(val, dict):
            return next(iter(val.values()), None)
        return val

    for fname in os.listdir(result_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(result_dir, fname), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if res["throughput"] is None:
            res["throughput"] = _parse_ms_value(_get_common(data, "Request Throughput"))
        if res["total_requests"] is None:
            res["total_requests"] = _get_common(data, "Total Requests")
        if res["success_requests"] is None:
            res["success_requests"] = _get_common(data, "Success Requests")

    for fname in os.listdir(result_dir):
        if not fname.endswith(".csv"):
            continue
        try:
            with open(os.path.join(result_dir, fname), encoding="utf-8") as f:
                rows = list(csv.reader(f))
        except OSError:
            continue
        if not rows:
            continue
        header = rows[0]
        pxx_cols = {}
        for i, h in enumerate(header):
            if PERCENTILE_PATTERN.match(h):
                pxx_cols[h] = i
        for row in rows[1:]:
            if not row:
                continue
            metric = row[0]
            for pxx, col in pxx_cols.items():
                if col >= len(row):
                    continue
                val = _parse_ms_value(row[col])
                if val is None:
                    continue
                if metric == "TTFT" and pxx in dict(ttft_limits):
                    res["ttft_pxx"][pxx] = val
                elif metric == "TPOT" and pxx in dict(tpot_limits):
                    res["tpot_pxx"][pxx] = val
    return res


# ---------------------------------------------------------------------------
# 每轮执行
# ---------------------------------------------------------------------------

def format_rate(rate):
    return f"{rate:g}"


def run_round(round_no, rate, args, ttft_limits, tpot_limits, logs_dir):
    """执行一轮 ais_bench 测试并监控/判定，返回本轮记录 dict。"""
    log_path = os.path.join(logs_dir, f"round_{format_rate(rate)}.log")
    cmd = [args.shell, args.script, "--request-rate", format_rate(rate)]
    print(f"[Round {round_no}] request_rate={format_rate(rate)}")
    print(f"[Round {round_no}] cmd: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=(os.name != "nt"),
    )
    collector = OutputCollector(proc, log_path)
    collector.start()

    monitor = None
    monitor_dir = None
    interrupted = False
    expected_total = args.expected_requests
    start_time = time.time()

    while True:
        if monitor_dir is None:
            monitor_dir = resolve_monitor_dir(collector, args)
            if monitor_dir is not None:
                print(f"[Round {round_no}] 监控落盘性能数据目录: {monitor_dir}")
                monitor = PerfMonitor(monitor_dir)

        if expected_total is None and collector.expected_total is not None:
            expected_total = collector.expected_total
            print(f"[Round {round_no}] 解析到预期总请求数: {expected_total}")

        if monitor is not None:
            monitor.scan()
            ok, reason, _details = requirement_1(
                [s[0] for s in monitor.samples],
                [s[1] for s in monitor.samples],
                ttft_limits, tpot_limits,
                expected_total=expected_total,
                early_abort_ratio=args.early_abort_ratio,
                min_samples=args.min_samples,
                finished=False,
            )
            if not ok:
                print(f"[Round {round_no}] 要求1不满足，打断本轮: {reason}")
                terminate_process(proc)
                interrupted = True

        if interrupted or proc.poll() is not None:
            break
        time.sleep(args.monitor_interval)

    collector.join(timeout=5)
    duration = time.time() - start_time

    # ---- 收尾：确定最终样本（优先用落盘 details 数据，其次用监控累积数据）----
    if monitor is not None:
        monitor.scan()
    result_dir = collector.result_dir
    if result_dir is None and monitor_dir is not None:
        result_dir = os.path.dirname(monitor_dir)  # 回退：监控目录的上一级
    details_samples = compute_samples_from_details(result_dir) if result_dir else []
    if details_samples:
        final_ttft = [s[0] for s in details_samples]
        final_tpot = [s[1] for s in details_samples]
    elif monitor is not None:
        final_ttft = [s[0] for s in monitor.samples]
        final_tpot = [s[1] for s in monitor.samples]
    else:
        final_ttft, final_tpot = [], []

    # ---- 最终要求1判定（打断轮次一律判为不通过）----
    ok, reason, details = requirement_1(
        final_ttft, final_tpot, ttft_limits, tpot_limits,
        expected_total=expected_total,
        early_abort_ratio=args.early_abort_ratio,
        min_samples=args.min_samples,
        finished=True,
    )
    passed = ok and not interrupted
    if interrupted:
        reason = f"提前打断: {reason}" if reason else "提前打断"

    # ---- 汇总数值 ----
    final_res = read_final_results(result_dir, ttft_limits, tpot_limits) if result_dir else {}
    requests_completed = len(final_ttft)
    throughput = final_res.get("throughput")
    ttft_pxx_vals, tpot_pxx_vals = {}, {}
    for pxx, _ in ttft_limits:
        v = final_res.get("ttft_pxx", {}).get(pxx)
        if v is None and final_ttft:
            v = round(float(np.percentile(final_ttft, int(pxx[1:]))), 4)
        ttft_pxx_vals[pxx] = v
    for pxx, _ in tpot_limits:
        v = final_res.get("tpot_pxx", {}).get(pxx)
        if v is None and final_tpot:
            v = round(float(np.percentile(final_tpot, int(pxx[1:]))), 4)
        tpot_pxx_vals[pxx] = v

    # 违反比例（相对已完成样本，用于 CSV 记录）
    vratio = {}
    for metric_name, samples, limits in (
        ("TTFT", final_ttft, ttft_limits),
        ("TPOT", final_tpot, tpot_limits),
    ):
        for pxx, th in limits:
            n = int(sum(1 for v in samples if v > th)) if samples else 0
            vratio[f"{metric_name}_{pxx}"] = round(n / len(samples), 4) if samples else None

    # ---- 打印本轮关键日志（需求2：至少含请求数与 Request Throughput）----
    summary_parts = [
        f"[Round {round_no}] request_rate={format_rate(rate)}",
        f"requests_completed={requests_completed}",
        f"expected_total={expected_total if expected_total is not None else 'unknown'}",
        f"request_throughput={throughput if throughput is not None else 'N/A'} req/s",
    ]
    summary_parts += [f"ttft_{pxx}={ttft_pxx_vals.get(pxx)}ms" for pxx, _ in ttft_limits]
    summary_parts += [f"tpot_{pxx}={tpot_pxx_vals.get(pxx)}ms" for pxx, _ in tpot_limits]
    summary_parts += [
        f"passed={passed}", f"interrupted={interrupted}",
        f"duration={duration:.1f}s", f"result_dir={result_dir or 'N/A'}",
    ]
    print(" ".join(summary_parts))

    # ---- 构造 CSV 记录 ----
    record = {
        "round": round_no,
        "request_rate": format_rate(rate),
        "passed": "True" if passed else "False",
        "interrupted": "True" if interrupted else "False",
        "requests_completed": requests_completed,
        "expected_total": expected_total if expected_total is not None else "",
        "request_throughput_req_s": throughput if throughput is not None else "",
    }
    for pxx, _ in ttft_limits:
        record[f"ttft_{pxx}_ms"] = ttft_pxx_vals.get(pxx, "")
    for pxx, _ in tpot_limits:
        record[f"tpot_{pxx}_ms"] = tpot_pxx_vals.get(pxx, "")
    for pxx, _ in ttft_limits:
        record[f"vratio_TTFT_{pxx}"] = vratio.get(f"TTFT_{pxx}", "")
    for pxx, _ in tpot_limits:
        record[f"vratio_TPOT_{pxx}"] = vratio.get(f"TPOT_{pxx}", "")
    record["result_dir"] = result_dir or ""
    record["log_path"] = log_path
    record["note"] = reason
    record["_duration_s"] = round(duration, 1)
    record["_ttft_samples"] = final_ttft
    record["_tpot_samples"] = final_tpot
    return record


# ---------------------------------------------------------------------------
# CLI 与主流程
# ---------------------------------------------------------------------------

def build_csv_columns(ttft_limits, tpot_limits):
    cols = [
        "round", "request_rate", "passed", "interrupted",
        "requests_completed", "expected_total", "request_throughput_req_s",
    ]
    cols += [f"ttft_{pxx}_ms" for pxx, _ in ttft_limits]
    cols += [f"tpot_{pxx}_ms" for pxx, _ in tpot_limits]
    cols += [f"vratio_TTFT_{pxx}" for pxx, _ in ttft_limits]
    cols += [f"vratio_TPOT_{pxx}" for pxx, _ in tpot_limits]
    cols += ["result_dir", "log_path", "note"]
    return cols


def append_csv(csv_path, columns, record):
    new_file = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(record)


def _limit_type(value):
    m = re.match(r"^(P\d{1,2}):(\d+(?:\.\d+)?)$", value.strip())
    if not m or not PERCENTILE_PATTERN.match(m.group(1)):
        raise argparse.ArgumentTypeError(
            f"约束对格式非法: {value!r}，应为 PXX:门限(ms)，如 P90:2000"
        )
    return (m.group(1), float(m.group(2)))


def normalize_limits(pairs):
    """同一指标内：同一百分位取更严（更小）门限，并按百分位升序排列。"""
    merged = {}
    for pxx, val in pairs:
        merged[pxx] = min(val, merged.get(pxx, float("inf")))
    return sorted(merged.items(), key=lambda kv: int(kv[0][1:]))


def parse_args():
    parser = argparse.ArgumentParser(
        description="为 ais_bench 性能测试任务寻优 request rate（满足要求1的最高 rate）"
    )
    parser.add_argument("--script", required=True,
                        help='含 ais_bench 基准命令的 shell 脚本路径（脚本须以 "$@" 透传追加参数）')
    parser.add_argument("--rate-min", type=float, required=True, help="寻优范围下限")
    parser.add_argument("--rate-max", type=float, required=True, help="寻优范围上限")
    parser.add_argument("--ttft-threshold", type=_limit_type, action="append", required=True,
                        metavar="PXX:ms", help="TTFT 门限约束对，如 P90:2000；可重复传入（如 P90/P95 各有门限）")
    parser.add_argument("--tpot-threshold", type=_limit_type, action="append", required=True,
                        metavar="PXX:ms", help="TPOT 门限约束对，如 P90:50；可重复传入")
    parser.add_argument("--expected-requests", type=int, default=None,
                        help="预期总请求数（提前打断判定分母）；缺省自动从看板进度解析")
    parser.add_argument("--early-abort-ratio", type=float, default=None,
                        help="全局允许违反比例；缺省按各百分位推导 (100-PXX)/100")
    parser.add_argument("--min-samples", type=int, default=10,
                        help="已完成样本数低于该值时不提前打断（默认 10）")
    parser.add_argument("--monitor-interval", type=float, default=5.0,
                        help="监控轮询间隔秒（默认 5）")
    parser.add_argument("--descent-factor", type=float, default=0.5,
                        help="算法1 等比下降因子（默认 0.5）")
    parser.add_argument("--tol", type=float, default=0.05,
                        help="算法1 收敛相对容差（默认 0.05，相对 rate-max）")
    parser.add_argument("--max-rounds", type=int, default=20, help="最大轮次兜底（默认 20）")
    parser.add_argument("--work-dir", type=str, default="outputs/default",
                        help="ais_bench 工作目录（默认 outputs/default，用于回退定位实验目录）")
    parser.add_argument("--output-dir", type=str, default="outputs/rate_search",
                        help="汇总 CSV 与轮次日志输出目录（默认 outputs/rate_search）")
    parser.add_argument("--model-abbr", type=str, default=None,
                        help="指定监控的模型 abbr；缺省自动取 performances 下最新子目录")
    parser.add_argument("--shell", type=str, default="bash",
                        help="执行脚本的 shell（默认 bash）")
    args = parser.parse_args()

    if args.rate_min <= 0:
        parser.error("--rate-min 必须为正数")
    if args.rate_min >= args.rate_max:
        parser.error("--rate-min 必须小于 --rate-max")
    if not os.path.isfile(args.script):
        parser.error(f"--script 文件不存在: {args.script}")
    if args.min_samples < 1:
        parser.error("--min-samples 必须 >= 1")
    if args.monitor_interval <= 0:
        parser.error("--monitor-interval 必须为正数")
    if args.early_abort_ratio is not None and not 0 < args.early_abort_ratio < 1:
        parser.error("--early-abort-ratio 必须在 (0, 1) 之间")
    return args


def main():
    args = parse_args()
    ttft_limits = normalize_limits(args.ttft_threshold)
    tpot_limits = normalize_limits(args.tpot_threshold)
    print(f"要求1 约束: TTFT={ttft_limits}, TPOT={tpot_limits}")

    logs_dir = os.path.join(args.output_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    csv_path = os.path.join(args.output_dir, "summary.csv")
    csv_columns = build_csv_columns(ttft_limits, tpot_limits)

    history = []
    round_no = 0
    try:
        while round_no < args.max_rounds:
            rate = algorithm_1(
                history, args.rate_min, args.rate_max,
                tol=args.tol, descent_factor=args.descent_factor,
            )
            if rate is None:
                break
            round_no += 1
            record = run_round(round_no, rate, args, ttft_limits, tpot_limits, logs_dir)
            history.append({
                "rate": rate,
                "passed": record["passed"] == "True",
                "interrupted": record["interrupted"] == "True",
                "record": record,
            })
            append_csv(csv_path, csv_columns, record)
    except KeyboardInterrupt:
        print("\n用户中断，正在退出...")
        sys.exit(130)

    # ---- 汇总输出 ----
    print("\n=== 寻优结束 ===")
    print(f"汇总 CSV 路径: {os.path.abspath(csv_path)}")
    if not history:
        print("未执行任何轮次")
        return
    passing = [h for h in history if h["passed"]]
    if passing:
        best = max(passing, key=lambda h: h["rate"])
        rec = best["record"]
        print(f"最优 request rate: {format_rate(best['rate'])} "
              f"(request_throughput={rec.get('request_throughput_req_s') or 'N/A'} req/s)")
    else:
        print(f"范围内 [{args.rate_min}, {args.rate_max}] 未找到满足要求1的 request rate")


if __name__ == "__main__":
    main()
