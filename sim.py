#!/usr/bin/env python3
# edge_queue_sim_all_in_one.py — DEBUG-friendly CLI (flags work before/after subcommand)
from __future__ import annotations
import math, random, itertools, csv, argparse, json, sys, logging
from dataclasses import dataclass, field
from heapq import heappush, heappop
from typing import List, Optional, Callable, Dict, Any, Tuple

LOGGER = logging.getLogger("edge_sim")
if not LOGGER.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

@dataclass(order=True)
class Event:
    time: float
    sort_index: int = field(init=False, repr=False)
    kind: str = field(compare=False, default="arrival")
    server_id: Optional[int] = field(compare=False, default=None)
    task_id: Optional[int] = field(compare=False, default=None)
    def __post_init__(self): object.__setattr__(self, "sort_index", id(self))

@dataclass
class Task:
    id: int
    cls: str
    arrival_time: float
    service_time: float
    start_service_time: Optional[float] = None
    completion_time: Optional[float] = None
    origin_server: Optional[int] = None
    assigned_server: Optional[int] = None
    offloaded: bool = False
    net_delay: float = 0.0

@dataclass
class Server:
    id: int
    high_prio: List[int] = field(default_factory=list)
    low_prio: List[int] = field(default_factory=list)
    busy: bool = False
    current_task: Optional[int] = None
    total_busy_time: float = 0.0
    last_update_time: float = 0.0
    offloaded_in: int = 0
    offloaded_out: int = 0
    def q_len_including_service(self) -> int:
        return len(self.high_prio) + len(self.low_prio) + (1 if self.busy else 0)

def exp_time(rate: float) -> float:
    if rate <= 0: return float("inf")
    import math, random
    u = random.random(); return -math.log(1.0 - u) / rate

def policy_local_only(state: Dict[str, Any], new_task: Task) -> int: return new_task.origin_server
def policy_jsq(state: Dict[str, Any], new_task: Task) -> int:
    lens = [(s.q_len_including_service(), s.id) for s in state["servers"]]; return min(lens)[1]
def policy_threshold_offload(threshold: int):
    def inner(state: Dict[str, Any], new_task: Task) -> int:
        s = state["servers"][new_task.origin_server]
        return policy_jsq(state, new_task) if s.q_len_including_service() > threshold else s.id
    return inner
def policy_prob_offload(p: float):
    def inner(state: Dict[str, Any], new_task: Task) -> int:
        import random
        return policy_jsq(state, new_task) if random.random() < p else new_task.origin_server
    return inner

class EdgeQueueSim:
    def __init__(self, n_servers=3, lam=4.0, p_short=0.8, mu_short=1/1.0, mu_long=1/10.0,
                 route_policy: Callable=policy_local_only, warmup_time=200.0, sim_time=2200.0,
                 seed: Optional[int]=None, tie_to_origin=True, net_delay_sampler=None,
                 logger: Optional[logging.Logger]=None, debug=False, debug_every=100):
        self.n_servers, self.lam, self.p_short = n_servers, lam, p_short
        self.mu_short, self.mu_long, self.route_policy = mu_short, mu_long, route_policy
        self.warmup_time, self.sim_time, self.seed = warmup_time, sim_time, seed
        self.tie_to_origin = tie_to_origin
        self.net_delay_sampler = net_delay_sampler or (lambda: 0.0)
        self.logger = logger or LOGGER; self.debug = debug; self.debug_every = max(1, int(debug_every))
        self._event_seq = 0
        self.clock = 0.0; self.event_q: List[Event] = []
        self.servers = [Server(id=i) for i in range(n_servers)]
        self.tasks: Dict[int, Task] = {}; self.next_task_id = 0
        if seed is not None: random.seed(seed)
    def _d(self, msg: str):
        if self.debug and (self._event_seq % self.debug_every) == 0:
            self.logger.debug(f"[t={self.clock:.3f} ev#{self._event_seq}] {msg}")
    def _snapshot(self) -> str:
        return " ".join(f"S{s.id}(H={len(s.high_prio)},L={len(s.low_prio)},busy={int(s.busy)})" for s in self.servers)
    def schedule(self, t, kind, server_id=None, task_id=None):
        heappush(self.event_q, Event(time=t, kind=kind, server_id=server_id, task_id=task_id))
        if self.debug: self.logger.debug(f"[t={self.clock:.3f} ev#{self._event_seq}] schedule({kind}@{t:.3f}, srv={server_id}, task={task_id})")
    def pick_origin(self) -> int: return random.randrange(self.n_servers)
    def sample_task(self, arrival_time, origin_server):
        import random
        cls = "short" if random.random() < self.p_short else "long"
        rate = self.mu_short if cls == "short" else self.mu_long
        st = exp_time(rate); task = Task(id=self.next_task_id, cls=cls, arrival_time=arrival_time, service_time=st, origin_server=origin_server)
        self.tasks[self.next_task_id] = task; self.next_task_id += 1
        self._d(f"NEW task#{task.id} cls={task.cls} st={task.service_time:.3f} origin=S{origin_server}")
        return task
    def enqueue_task(self, server: Server, task: Task):
        (server.high_prio if task.cls=="short" else server.low_prio).append(task.id)
        self._d(f"ENQ task#{task.id} -> S{server.id} prio={'H' if task.cls=='short' else 'L'} | {self._snapshot()}")
        self.try_start_service(server)
    def try_start_service(self, server: Server):
        if server.busy: return
        if server.high_prio: tid = server.high_prio.pop(0)
        elif server.low_prio: tid = server.low_prio.pop(0)
        else: return
        t = self.tasks[tid]; server.busy = True; server.current_task = tid
        t.start_service_time = self.clock; server.last_update_time = self.clock
        self._d(f"START S{server.id} <= task#{t.id} ({t.cls}) st={t.service_time:.3f}")
        self.schedule(self.clock + t.service_time, "departure", server_id=server.id, task_id=tid)
    def update_busy_time(self, server: Server):
        if server.busy: server.total_busy_time += self.clock - server.last_update_time
        server.last_update_time = self.clock
    def _compute_metrics(self, completed_tasks: List[Task]) -> Dict[str, float]:
        filt = [t for t in completed_tasks if t.arrival_time >= self.warmup_time]; n = len(filt)
        if n == 0:
            return {"overall_mean_response": float("nan"), "short_mean_response": float("nan"), "long_mean_response": float("nan"),
                    "overall_mean_wait": float("nan"), "throughput": 0.0, "offload_rate": float("nan"), "mean_net_delay_offloaded": float("nan")}
        resp_sum = wait_sum = short_resp_sum = long_resp_sum = 0.0; n_short = n_long = 0; offloaded_delays = []; offloaded_count = 0
        for t in filt:
            response = t.completion_time - t.arrival_time
            wait = (t.start_service_time if t.start_service_time is not None else t.arrival_time) - t.arrival_time
            resp_sum += response; wait_sum += wait
            if t.cls == "short": short_resp_sum += response; n_short += 1
            else: long_resp_sum += response; n_long += 1
            if t.offloaded: offloaded_count += 1; offloaded_delays.append(t.net_delay)
        overall_mean_response = resp_sum / n
        short_mean_response = (short_resp_sum / n_short) if n_short > 0 else float("nan")
        long_mean_response = (long_resp_sum / n_long) if n_long > 0 else float("nan")
        overall_mean_wait = wait_sum / n
        throughput = n / max(self.sim_time - self.warmup_time, 1e-9)
        offload_rate = (offloaded_count / n) if n > 0 else float("nan")
        mean_net_delay_offloaded = (sum(offloaded_delays)/len(offloaded_delays)) if offloaded_delays else float("nan")
        return {"overall_mean_response": overall_mean_response, "short_mean_response": short_mean_response,
                "long_mean_response": long_mean_response, "overall_mean_wait": overall_mean_wait,
                "throughput": throughput, "offload_rate": offload_rate, "mean_net_delay_offloaded": mean_net_delay_offloaded}
    def run(self) -> Dict[str, Any]:
        self.schedule(exp_time(self.lam), "arrival")
        while self.event_q and self.clock <= self.sim_time:
            ev = heappop(self.event_q); self._event_seq += 1; self.clock = ev.time
            if self.debug: self.logger.debug(f"[t={self.clock:.3f} ev#{self._event_seq}] POP {ev.kind} srv={ev.server_id} task={ev.task_id} | {self._snapshot()}")
            if ev.kind == "arrival":
                origin = self.pick_origin() if self.tie_to_origin else 0
                task = self.sample_task(self.clock, origin_server=origin)
                dest = self.route_policy({"servers": self.servers, "time": self.clock}, task); task.assigned_server = dest
                if dest != origin:
                    task.offloaded = True; self.servers[origin].offloaded_out += 1; self.servers[dest].offloaded_in += 1
                    task.net_delay = self.net_delay_sampler() if self.net_delay_sampler else 0.0
                    self._d(f"ROUTE task#{task.id} S{origin} -> S{dest} OFFLOAD delay={task.net_delay:.3f}")
                    self.schedule(self.clock + task.net_delay, "enqueue", server_id=dest, task_id=task.id)
                else:
                    self._d(f"ROUTE task#{task.id} S{origin} -> S{dest} local")
                    self.schedule(self.clock, "enqueue", server_id=dest, task_id=task.id)
                nxt = self.clock + exp_time(self.lam)
                if nxt <= self.sim_time: self.schedule(nxt, "arrival")
            elif ev.kind == "enqueue":
                s = self.servers[ev.server_id]; task = self.tasks[ev.task_id]; self.enqueue_task(s, task)
            elif ev.kind == "departure":
                s = self.servers[ev.server_id]; self.update_busy_time(s)
                t = self.tasks[ev.task_id]; t.completion_time = self.clock
                self._d(f"DONE S{s.id} -> task#{t.id} ({t.cls}) resp={t.completion_time - t.arrival_time:.3f}")
                s.busy = False; s.current_task = None; self.try_start_service(s)
        for s in self.servers: self.update_busy_time(s)
        completed = [t for t in self.tasks.values() if t.completion_time is not None]
        if self.debug:
            summary = " | ".join(f"S{s.id}: util={s.total_busy_time/max(self.sim_time,1e-9):.3f} in={s.offloaded_in} out={s.offloaded_out} endQ={s.q_len_including_service()}" for s in self.servers)
            self.logger.debug("[SUMMARY] " + summary)
        return {"tasks": completed, "servers": self.servers, "metrics": self._compute_metrics(completed)}

def _parse_policy(spec: Any) -> Tuple[Callable, str]:
    if callable(spec): return spec, getattr(spec, "__name__", "callable")
    s = str(spec).strip().lower()
    if s == "local": return policy_local_only, "local"
    if s == "jsq": return policy_jsq, "jsq"
    if s.startswith("threshold:"):
        k = int(s.split(":",1)[1]); return policy_threshold_offload(k), f"threshold:{k}"
    if s.startswith("prob:"):
        p = float(s.split(":",1)[1]); return policy_prob_offload(p), f"prob:{p}"
    raise ValueError(f"Unrecognized policy spec: {spec!r}")

def _exp_net_sampler(mean_delay: Optional[float]) -> Tuple[Callable[[], float], float]:
    if mean_delay is None or mean_delay <= 0: return (lambda: 0.0), 0.0
    rate = 1.0/mean_delay; return (lambda: exp_time(rate)), float(mean_delay)

def sweep(param_grid: Dict[str, List[Any]], base_kwargs: Optional[Dict[str, Any]] = None, runs: int = 1, seed: int = 123) -> List[Dict[str, Any]]:
    base = dict(n_servers=3, lam=4.5, p_short=0.8, mu_short=1/1.0, mu_long=1/10.0, sim_time=3000.0, warmup_time=300.0, tie_to_origin=True)
    if base_kwargs: base.update(base_kwargs)
    if not param_grid: param_grid = {}
    grid_keys = sorted(param_grid.keys()); grid_vals = [param_grid[k] for k in grid_keys] if grid_keys else [[]]
    rows: List[Dict[str, Any]] = []
    for combo in (itertools.product(*grid_vals) if grid_keys else [()]):
        params = dict(zip(grid_keys, combo))
        pol_spec = params.get("policy", base.get("route_policy", "jsq"))
        policy_fn, pol_name = _parse_policy(pol_spec) if not callable(pol_spec) else (pol_spec, str(pol_spec))
        net_sampler, nd_mean = _exp_net_sampler(params.get("net_delay_mean", base.get("net_delay_mean", 0.0)))
        sim_kwargs = dict(base); sim_kwargs.update(params)
        sim_kwargs.pop("policy", None); sim_kwargs.pop("net_delay_mean", None)
        sim_kwargs["route_policy"] = policy_fn; sim_kwargs["net_delay_sampler"] = net_sampler
        metr_accum: Optional[Dict[str, float]] = None
        for r in range(runs):
            sim = EdgeQueueSim(n_servers=sim_kwargs["n_servers"], lam=sim_kwargs["lam"], p_short=sim_kwargs["p_short"],
                               mu_short=sim_kwargs["mu_short"], mu_long=sim_kwargs["mu_long"],
                               route_policy=sim_kwargs["route_policy"], warmup_time=sim_kwargs["warmup_time"],
                               sim_time=sim_kwargs["sim_time"], seed=(seed+r), tie_to_origin=sim_kwargs.get("tie_to_origin", True),
                               net_delay_sampler=sim_kwargs["net_delay_sampler"], logger=LOGGER, debug=False)
            m = sim.run()["metrics"]
            if metr_accum is None: metr_accum = {k: float(m[k]) for k in m}
            else:
                for k in metr_accum: metr_accum[k] += float(m[k])
        for k in metr_accum: metr_accum[k] /= runs
        row: Dict[str, Any] = {"policy": pol_name, "net_delay_mean": nd_mean, "n_servers": sim_kwargs["n_servers"],
                               "lam": sim_kwargs["lam"], "p_short": sim_kwargs["p_short"],
                               "mu_short": sim_kwargs["mu_short"], "mu_long": sim_kwargs["mu_long"],
                               "sim_time": sim_kwargs["sim_time"], "warmup_time": sim_kwargs["warmup_time"]}
        row.update(metr_accum); rows.append(row)
    return rows

def sweep_to_csv(rows: List[Dict[str, Any]], path: str) -> str:
    if not rows: open(path, "w").close(); return path
    headers = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers); w.writeheader()
        for r in rows: w.writerow(r)
    return path

def plot_response_vs_lambda(rows: List[Dict[str, Any]], path: Optional[str] = None) -> Optional[str]:
    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        print("[plot] matplotlib not available; skipping plot.", file=sys.stderr); return None
    by_policy: Dict[str, List[Tuple[float, float]]] = {}
    for r in rows:
        pol = str(r["policy"]); by_policy.setdefault(pol, []).append((float(r["lam"]), float(r["overall_mean_response"])))
    plt.figure(figsize=(7,4.2))
    for pol, series in sorted(by_policy.items()):
        series = sorted(series, key=lambda x: x[0])
        xs = [x for x,_ in series]; ys = [y for _,y in series]
        plt.plot(xs, ys, marker="o", label=pol)
    plt.xlabel("Arrival rate λ"); plt.ylabel("Overall mean response time"); plt.title("Response vs λ (by policy)")
    plt.legend(); plt.tight_layout()
    if path: plt.savefig(path, dpi=150); return path
    else: plt.show(); return None

def _comma_list_to_vals(s: str, cast: Callable[[str], Any]) -> List[Any]:
    return [cast(x) for x in s.split(",")] if s else []

def _print_json(obj: Any): print(json.dumps(obj, indent=2, sort_keys=True))

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Edge queueing simulator (priorities + offloading + latency)")
    # Define shared flags on the TOP-LEVEL parser so they can appear BEFORE the subcommand:
    p.add_argument("--debug", action="store_true", help="enable DEBUG logs")
    p.add_argument("--log-level", dest="log_level", type=str,
                   choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL"], default=None, help="set log level")
    p.add_argument("--debug-every", type=int, default=100, help="log every Nth event to throttle output")

    # Also provide a parent parser so shared flags can appear AFTER the subcommand:
    parent = argparse.ArgumentParser(add_help=False)
    for a in p._actions[-3:]:  # hacky but reliable: reuse last three added options
        if a.option_strings:
            parent.add_argument(*a.option_strings, **{k:v for k,v in a.__dict__.items()
                                                      if k in ("dest","type","default","choices","help","action")})

    sub = p.add_subparsers(dest="mode", required=True)

    s1 = sub.add_parser("single", parents=[parent], help="Run one simulation and print metrics")
    s1.add_argument("--n-servers", type=int, default=3)
    s1.add_argument("--lam", type=float, default=4.5)
    s1.add_argument("--p-short", type=float, default=0.8)
    s1.add_argument("--mu-short", type=float, default=1.0)     # mean service time for short
    s1.add_argument("--mu-long", type=float, default=10.0)     # mean service time for long
    s1.add_argument("--policy", type=str, default="jsq", help="local | jsq | threshold:K | prob:P")
    s1.add_argument("--net-delay-mean", type=float, default=0.0, help="mean offload delay (exp); 0 disables")
    s1.add_argument("--sim-time", type=float, default=3000.0)
    s1.add_argument("--warmup-time", type=float, default=300.0)
    s1.add_argument("--seed", type=int, default=42)
    s1.add_argument("--print-servers", action="store_true", help="also print per-server stats")

    s2 = sub.add_parser("sweep", parents=[parent], help="Run a parameter sweep and print/optionally save results")
    s2.add_argument("--n-servers", type=str, default="3")
    s2.add_argument("--lam", type=str, default="4.5")
    s2.add_argument("--p-short", type=str, default="0.8")
    s2.add_argument("--mu-short", type=str, default="1.0")
    s2.add_argument("--mu-long", type=str, default="10.0")
    s2.add_argument("--policy", type=str, default="jsq")
    s2.add_argument("--net-delay-mean", type=str, default="0.0")
    s2.add_argument("--sim-time", type=str, default="3000.0")
    s2.add_argument("--warmup-time", type=str, default="300.0")
    s2.add_argument("--runs", type=int, default=2)
    s2.add_argument("--seed", type=int, default=101)
    s2.add_argument("--csv-out", type=str, default="", help="optional CSV path to save results")
    s2.add_argument("--plot-out", type=str, default="", help="optional PNG path for plot (requires matplotlib)")

    return p

def _apply_logging_flags(args):
    if args.log_level:
        level_name = args.log_level.upper()
    elif args.debug:
        level_name = "DEBUG"
    else:
        level_name = "INFO"
    level = getattr(logging, level_name, logging.INFO)
    LOGGER.setLevel(level)

def main(argv: Optional[List[str]] = None):
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    _apply_logging_flags(args)

    if args.mode == "single":
        policy_fn, _ = _parse_policy(args.policy)
        net_sampler, _ = _exp_net_sampler(args.net_delay_mean)
        sim = EdgeQueueSim(n_servers=int(args.n_servers), lam=float(args.lam), p_short=float(args.p_short),
                           mu_short=1.0/float(args.mu_short), mu_long=1.0/float(args.mu_long),
                           route_policy=policy_fn, warmup_time=float(args.warmup_time), sim_time=float(args.sim_time),
                           seed=int(args.seed), tie_to_origin=True, net_delay_sampler=net_sampler,
                           logger=LOGGER, debug=args.debug or (args.log_level=="DEBUG"), debug_every=int(args.debug_every))
        out = sim.run()
        print("== METRICS =="); _print_json(out["metrics"])
        if args.print_servers:
            print("\n== SERVER STATS ==")
            for s in out["servers"]:
                util = s.total_busy_time / max(sim.sim_time, 1e-9)
                print(f"server={s.id} util={util:.3f} offloaded_in={s.offloaded_in} offloaded_out={s.offloaded_out} q_len_end={s.q_len_including_service()}")

    elif args.mode == "sweep":
        grid: Dict[str, List[Any]] = {}
        def add_if_any(key: str, raw: str, caster: Callable[[str], Any]):
            vals = [caster(x) for x in raw.split(",")] if raw else []
            if vals: grid[key] = vals
        add_if_any("n_servers", args.n_servers, int)
        add_if_any("lam", args.lam, float)
        add_if_any("p_short", args.p_short, float)
        add_if_any("mu_short", args.mu_short, float)
        add_if_any("mu_long", args.mu_long, float)
        add_if_any("policy", args.policy, str)
        add_if_any("net_delay_mean", args.net_delay_mean, float)
        add_if_any("sim_time", args.sim_time, float)
        add_if_any("warmup_time", args.warmup_time, float)
        base_kwargs = {}
        def first_or_none(raw: str) -> Optional[float]:
            return float(raw.split(",")[0]) if raw else None
        ms = first_or_none(args.mu_short); ml = first_or_none(args.mu_long)
        if ms is not None: base_kwargs["mu_short"] = 1.0/ms
        if ml is not None: base_kwargs["mu_long"] = 1.0/ml
        if "mu_short" in grid: grid["mu_short"] = [1.0/x for x in grid["mu_short"]]
        if "mu_long" in grid: grid["mu_long"] = [1.0/x for x in grid["mu_long"]]
        rows = sweep(param_grid=grid, base_kwargs=base_kwargs, runs=int(args.runs), seed=int(args.seed))
        print(f"== SWEEP RESULTS ({len(rows)} rows) =="); _print_json(rows[:5] + ([{'...':'truncated'}] if len(rows)>5 else []))
        if args.csv_out: sweep_to_csv(rows, args.csv_out); print(f"CSV saved to: {args.csv_out}")
        if args.plot_out:
            out_path = plot_response_vs_lambda(rows, args.plot_out)
            if out_path: print(f"Plot saved to: {out_path}")

if __name__ == "__main__":
    main()

# python edge_queue_sim.py single --lam 4.5 --policy jsq --net-delay-mean 0.2 --debug --debug-every 100
# python edge_queue_sim.py single --lam 3.5 --policy threshold:3 --net-delay-mean 0.1 --debug-every 1 --log-level DEBUG
# python edge_queue_sim.py sweep  --lam 3,4,5,6 --policy local,jsq,threshold:3,prob:0.3 --net-delay-mean 0.2 --runs 2 --csv-out sweep.csv
