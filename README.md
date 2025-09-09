# Edge Queue Simulator

Exploring the use of queuing theory as a surrogate model in federated prompt learning

A single-file discrete-event simulator for **edge computing** with:

- Two task classes: **short** and **long** (long ≈ 10× short by default).
- **Non‑preemptive priority** scheduling on each server (short > long; FCFS within class).
- **Offloading** across servers via routing policies (local, JSQ, threshold, probabilistic).
- **Network latency** for offloaded tasks.
- **Sweeps** over parameters with CSV export and optional plotting.
- **Debug logging** (throttled) so you can trace the event flow without drowning in logs.

> Main file: `edge_queue_sim_all_in_one.py` (Python 3.8+).  
> No third‑party deps unless you want plots (`matplotlib`).

---

## Quick start

**Single run (quiet):**
```bash
python edge_queue_sim_all_in_one.py single \
  --lam 4.5 --p-short 0.8 \
  --mu-short 1.0 --mu-long 10.0 \
  --policy jsq \
  --net-delay-mean 0.2
```

**Single run (debug every 100th event):**
```bash
python edge_queue_sim_all_in_one.py --log-level DEBUG --debug-every 100 single \
  --lam 4.5 --policy jsq --net-delay-mean 0.2
```
_Global flags can be placed **before or after** the subcommand._

**Sweep (λ × policy), save CSV and plot:**
```bash
python edge_queue_sim_all_in_one.py sweep \
  --lam 3,4,5,6 \
  --policy local,jsq,threshold:3,prob:0.3 \
  --net-delay-mean 0.2 \
  --runs 3 \
  --csv-out sweep_results.csv \
  --plot-out response_vs_lambda.png
```

---

## What the simulator models

- **Arrivals:** Poisson with rate `lam` (system‑wide). Each task randomly picks an origin server.
- **Service times:** Exponential by class. CLI takes **means** (`--mu-short`, `--mu-long`); the code converts them to rates internally. (If you import and use the Python API, you pass **rates**.)
- **Routing/offloading:** A policy decides the destination server; if destination ≠ origin, the task incurs a one‑way **network delay** before joining the destination queue.
- **Server discipline:** Each server is single‑threaded (M/G/1) with **non‑preemptive priority**: short‑class tasks queue ahead of long‑class tasks; an in‑service task is never interrupted.

---

## Inputs & Configuration

### Global flags (work before *or* after the subcommand)

| Flag | Type | Default | Description |
|---|---|---:|---|
| `--debug` | flag | `false` | Enable DEBUG logging. |
| `--log-level` | `DEBUG\|INFO\|WARNING\|ERROR\|CRITICAL` | `INFO` | Set log level explicitly. |
| `--debug-every` | int | `100` | Log every Nth event (throttles DEBUG). |

### `single` mode

| Flag | Type | Default | Meaning |
|---|---|---:|---|
| `--n-servers` | int | `3` | Number of edge servers. |
| `--lam` | float | `4.5` | Poisson arrival rate (system‑wide). |
| `--p-short` | float | `0.8` | Probability a task is short (`1-p` is long). |
| `--mu-short` | float | `1.0` | **Mean** short service time. |
| `--mu-long` | float | `10.0` | **Mean** long service time. |
| `--policy` | str | `jsq` | Routing: `local`, `jsq`, `threshold:K`, `prob:P`. |
| `--net-delay-mean` | float | `0.0` | Mean offload delay (exponential). |
| `--sim-time` | float | `3000.0` | Simulation horizon. |
| `--warmup-time` | float | `300.0` | Arrivals before this are excluded from metrics. |
| `--seed` | int | `42` | RNG seed. |
| `--print-servers` | flag | `false` | Print per‑server utilization and offload counts. |

**Policies:**
- `local`: keep tasks on origin server.
- `jsq`: join the shortest queue (queue + in‑service).
- `threshold:K`: if origin’s length (incl. service) > `K`, route via JSQ; else stay local.
- `prob:P`: with probability `P`, route via JSQ; else stay local.

### `sweep` mode

Provide **comma‑separated** lists; the Cartesian product defines the grid. Results are averaged over `--runs` independent seeds.

| Flag | Type | Default | Notes |
|---|---|---:|---|
| `--n-servers` | list[int] | `3` | e.g., `2,3,4` |
| `--lam` | list[float] | `4.5` | e.g., `3,4,5,6` |
| `--p-short` | list[float] | `0.8` | |
| `--mu-short` | list[float] | `1.0` | **Means** for short service time. |
| `--mu-long` | list[float] | `10.0` | **Means** for long service time. |
| `--policy` | list[str] | `jsq` | e.g., `local,jsq,threshold:3,prob:0.3` |
| `--net-delay-mean` | list[float] | `0.0` | e.g., `0,0.1,0.2` |
| `--sim-time` | list[float] | `3000.0` | |
| `--warmup-time` | list[float] | `300.0` | |
| `--runs` | int | `2` | Repetitions averaged per grid point. |
| `--seed` | int | `101` | Base seed (incremented each run). |
| `--csv-out` | str | empty | Write results to CSV. |
| `--plot-out` | str | empty | Save PNG of “response vs. λ” (needs `matplotlib`). |

---

## Outputs

The simulator prints a JSON block labeled **`== METRICS ==`** with:

- `overall_mean_response`
- `short_mean_response`, `long_mean_response`
- `overall_mean_wait`
- `throughput` (per unit time, post‑warmup)
- `offload_rate` (share of completed tasks that were offloaded)
- `mean_net_delay_offloaded` (mean one‑way delay among offloaded tasks)

If `--print-servers` is set, you’ll also get per‑server lines with utilization (`busy_time / sim_time`), offloaded in/out counts, and end‑of‑run queue lengths.

Sweeps return a list of rows (printed as JSON), and can be saved to CSV via `--csv-out`.

---

## Examples

**1) JSQ with latency, quiet**
```bash
python edge_queue_sim_all_in_one.py single \
  --lam 4.5 --p-short 0.8 \
  --mu-short 1.0 --mu-long 10.0 \
  --policy jsq \
  --net-delay-mean 0.2
```

**2) Threshold policy with small latency**
```bash
python edge_queue_sim_all_in_one.py single \
  --lam 5.0 --policy threshold:3 --net-delay-mean 0.1
```

**3) Debug run (logs every 50th event)**
```bash
python edge_queue_sim_all_in_one.py single \
  --lam 4.0 --policy jsq --net-delay-mean 0.2 \
  --log-level DEBUG --debug-every 50
```

**4) Sweep λ × policy, CSV + plot**
```bash
python edge_queue_sim_all_in_one.py sweep \
  --lam 3,4,5,6 \
  --policy local,jsq,threshold:3,prob:0.3 \
  --net-delay-mean 0.2 \
  --runs 3 \
  --csv-out sweep_results.csv \
  --plot-out response_vs_lambda.png
```

**5) Programmatic API**
```python
from edge_queue_sim_all_in_one import EdgeQueueSim, policy_jsq, exp_time

sim = EdgeQueueSim(
    n_servers=3,
    lam=4.5,
    p_short=0.8,
    mu_short=1/1.0,   # API takes rates
    mu_long=1/10.0,
    route_policy=policy_jsq,
    net_delay_sampler=lambda: exp_time(1/0.2),
    warmup_time=300,
    sim_time=3000,
    seed=42,
)
out = sim.run()
print(out["metrics"])
```

---

## Notes & Tips

- **Means vs rates:** CLI takes **means** for service times; the Python API takes **rates**.  
- **Priority discipline:** Non‑preemptive; short class has higher priority in the queue only (does not interrupt an active long job).  
- **Network delay:** Applied only when the destination server differs from the origin.  
- **Stability & variance:** For heavy traffic, increase `sim_time`/`warmup_time` and consider averaging over multiple seeds (`--runs`) in sweeps.
- **Plotting:** Install `matplotlib` to use `--plot-out`.

---

## Troubleshooting

- `argument mode: invalid choice: 'DEBUG' ...`  
  In this version, global flags work **before or after** the subcommand (e.g., `--log-level DEBUG single ...` or `single ... --log-level DEBUG`). If you still see this, make sure you didn’t accidentally put `DEBUG` where the subcommand should go.

- “Code Interpreter session expired” (ChatGPT sandbox reset): re‑upload `edge_queue_sim_all_in_one.py` or copy/paste from this chat and re‑run.

Happy simulating!
