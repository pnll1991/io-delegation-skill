#!/usr/bin/env python3
"""A/B Codex baseline vs io-delegation skill/hooks with success-adjusted token accounting."""
from __future__ import annotations
import argparse, csv, json, random, shlex, shutil, subprocess, sys, tempfile, time
from pathlib import Path


def run(argv, cwd=None, timeout=None, **kw):
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout,
                          text=True, check=False, **kw)


def git(repo, *args, timeout=120):
    return run(["git", *args], cwd=repo, timeout=timeout,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def usage_from_jsonl(path: Path):
    u = {k: 0 for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                         "output_tokens", "reasoning_output_tokens")}
    u.update(worker_calls=0, worker_input_tokens=0, worker_output_tokens=0,
             worker_usage_unknown_calls=0, worker_request_bytes=0,
             tool_items=0, command_items=0, mcp_items=0, subagent_items=0,
             thread_id=None)
    seen = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []:
        try: e = json.loads(line)
        except json.JSONDecodeError: continue
        if e.get("type") == "thread.started": u["thread_id"] = e.get("thread_id")
        elif e.get("type") == "turn.completed":
            x = e.get("usage") or {}
            for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                      "output_tokens", "reasoning_output_tokens"):
                u[k] += int(x.get(k, 0) or 0)
        elif e.get("type") == "item.completed":
            item = e.get("item") or {}; iid = item.get("id")
            if iid and iid in seen: continue
            if iid: seen.add(iid)
            typ = item.get("type")
            if typ == "command_execution":
                u["command_items"] += 1; u["tool_items"] += 1
                for raw in (item.get("aggregated_output") or "").splitlines():
                    try: m = json.loads(raw.strip())
                    except json.JSONDecodeError: continue
                    if not isinstance(m, dict) or m.get("event") != "worker_response": continue
                    u["worker_calls"] += 1
                    u["worker_request_bytes"] += int(m.get("request_bytes", 0) or 0)
                    wu = m.get("usage") or {}; wi, wo = wu.get("input_tokens"), wu.get("output_tokens")
                    if type(wi) is int and wi >= 0 and type(wo) is int and wo >= 0:
                        u["worker_input_tokens"] += wi; u["worker_output_tokens"] += wo
                    else: u["worker_usage_unknown_calls"] += 1
            elif typ == "mcp_tool_call": u["mcp_items"] += 1; u["tool_items"] += 1
            elif typ == "collab_tool_call": u["subagent_items"] += 1; u["tool_items"] += 1
            elif typ in {"web_search", "file_change"}: u["tool_items"] += 1
    return u


def fmt(value, ctx):
    try: return str(value).format_map(ctx)
    except KeyError as e: raise ValueError(f"unknown placeholder: {e.args[0]}") from e


def setup(worktree, strategy, manifest, manifest_path, repo, run_dir):
    ctx = {"worktree": str(worktree), "repo": str(repo), "manifest_dir": str(manifest_path.parent),
           "python": sys.executable, "strategy": strategy["name"]}
    ctx.update({str(k): str(v) for k, v in (manifest.get("variables") or {}).items()})
    rows = []
    for entry in strategy.get("setup_commands", []):
        started = time.monotonic(); shell = isinstance(entry, str)
        cmd = fmt(entry, ctx) if shell else [fmt(x, ctx) for x in entry]
        cp = run(cmd, cwd=worktree, timeout=int(strategy.get("setup_timeout_seconds", 180)), shell=shell,
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rows.append({"command": cmd, "exit_code": cp.returncode,
                     "wall_seconds": time.monotonic()-started,
                     "stdout_tail": cp.stdout[-6000:], "stderr_tail": cp.stderr[-6000:]})
        if cp.returncode:
            (run_dir/"setup.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            raise RuntimeError(f"setup failed: {cmd!r}\n{cp.stderr[-3000:]}")
    if rows: (run_dir/"setup.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return bool(rows)


def commit_setup(worktree):
    if git(worktree, "add", "-A").returncode: raise RuntimeError("git add setup failed")
    if git(worktree, "diff", "--cached", "--quiet").returncode == 0: return
    cp = run(["git", "-c", "user.name=Codex Benchmark", "-c", "user.email=bench@example.invalid",
              "commit", "--no-gpg-sign", "-m", "benchmark: strategy setup"], cwd=worktree,
             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if cp.returncode: raise RuntimeError(cp.stderr)


def check_commands(worktree, commands, timeout):
    rows=[]; ok=True
    for entry in commands:
        cmd = entry if isinstance(entry, str) else shlex.join([str(x) for x in entry])
        cp = subprocess.run(cmd, cwd=worktree, shell=True, text=True, timeout=timeout,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        passed = cp.returncode == 0; ok &= passed
        rows.append({"command": cmd, "ok": passed, "exit_code": cp.returncode,
                     "stdout_tail": cp.stdout[-6000:], "stderr_tail": cp.stderr[-6000:]})
    return ok, rows


def principal_cost(u, out_ratio):
    ordinary = max(0, u["input_tokens"]-u["cached_input_tokens"]-u["cache_write_input_tokens"])
    return ordinary + .10*u["cached_input_tokens"] + 1.25*u["cache_write_input_tokens"] + out_ratio*u["output_tokens"]


def system_cost(u, pc, strategy):
    if not u["worker_calls"]: return pc
    if u["worker_usage_unknown_calls"]: return None
    ir, or_ = strategy.get("worker_input_price_to_main_input"), strategy.get("worker_output_price_to_main_input")
    if ir is None or or_ is None: return None
    return pc + float(ir)*u["worker_input_tokens"] + float(or_)*u["worker_output_tokens"]


def aggregate(rows):
    result=[]
    for name in sorted({r["strategy"] for r in rows}):
        rs=[r for r in rows if r["strategy"]==name]; succ=sum(r["valid_success"] for r in rs)
        pc=sum(r["principal_cost_equiv"] for r in rs); complete=all(r["system_cost_equiv"] is not None for r in rs)
        sc=sum(r["system_cost_equiv"] for r in rs) if complete else None
        result.append({"strategy":name,"runs":len(rs),"valid_successes":succ,
          "valid_success_rate":succ/len(rs),"principal_CPTS":pc/succ if succ else None,
          "system_cost_complete":complete,"system_CPTS":sc/succ if complete and succ else None,
          "input_tokens":sum(r["input_tokens"] for r in rs),"cached_input_tokens":sum(r["cached_input_tokens"] for r in rs),
          "output_tokens":sum(r["output_tokens"] for r in rs),"reasoning_output_tokens":sum(r["reasoning_output_tokens"] for r in rs),
          "worker_calls":sum(r["worker_calls"] for r in rs),"worker_input_tokens":sum(r["worker_input_tokens"] for r in rs),
          "worker_output_tokens":sum(r["worker_output_tokens"] for r in rs),
          "worker_usage_unknown_calls":sum(r["worker_usage_unknown_calls"] for r in rs),
          "wall_seconds":sum(r["wall_seconds"] for r in rs)})
    return result


def summary(path, rows):
    a=aggregate(rows); b=next((x for x in a if x["strategy"]=="baseline"), a[0] if a else None)
    if not b: return
    lines=["# Codex × io-delegation A/B", "", "| Strategy | Success | Principal CPTS | Δ | System CPTS | Δ | Workers | Wall s |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for x in a:
        pc=x["principal_CPTS"]; sc=x["system_CPTS"]
        pd=((pc/b["principal_CPTS"])-1)*100 if pc is not None and b["principal_CPTS"] else None
        sd=((sc/b["system_CPTS"])-1)*100 if sc is not None and b["system_CPTS"] else None
        f=lambda v:"n/a" if v is None else f"{v:.1f}"; p=lambda v:"n/a" if v is None else f"{v:+.1f}%"
        lines.append(f"| {x['strategy']} | {x['valid_success_rate']:.0%} | {f(pc)} | {p(pd)} | {f(sc)} | {p(sd)} | {x['worker_calls']} | {x['wall_seconds']:.1f} |")
    lines += ["", "Negative delta is better only if acceptance/regression success is preserved.",
              "System CPTS is omitted when worker usage or relative worker pricing is unknown."]
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("manifest", type=Path); ap.add_argument("--output", type=Path, default=Path("codex-io-ab-results"))
    ap.add_argument("--seed", type=int, default=20260914); ap.add_argument("--strategy", action="append"); ap.add_argument("--task", action="append")
    args=ap.parse_args()
    if not shutil.which("codex") or not shutil.which("git"): raise SystemExit("git and authenticated codex CLI are required")
    mp=args.manifest.expanduser().resolve(); m=json.loads(mp.read_text(encoding="utf-8")); repo=Path(m["repo"]).expanduser().resolve()
    strategies=m["strategies"]; tasks=m["tasks"]
    if args.strategy: strategies=[x for x in strategies if x["name"] in set(args.strategy)]
    if args.task: tasks=[x for x in tasks if x["id"] in set(args.task)]
    out=args.output.resolve(); (out/"runs").mkdir(parents=True, exist_ok=True); reps=int(m.get("repetitions",1)); ratio=float(m.get("output_price_to_input_price_ratio",1))
    plan=[(t,s,r) for t in tasks for s in strategies for r in range(1,reps+1)]; random.Random(args.seed).shuffle(plan); rows=[]
    temp=Path(tempfile.mkdtemp(prefix="codex-io-ab-"))
    try:
        for n,(task,strategy,rep) in enumerate(plan,1):
            rid=f"{n:03d}-{task['id']}-{strategy['name']}-r{rep}"; rd=out/"runs"/rid; rd.mkdir(); wt=temp/rid
            base=task.get("base_commit",m.get("base_commit","HEAD")); cp=git(repo,"worktree","add","--detach",str(wt),str(base),timeout=180)
            if cp.returncode: raise RuntimeError(cp.stderr)
            try:
                for rel in m.get("forbid_base_paths",[]):
                    if (wt/rel).exists(): raise RuntimeError(f"contaminated base path: {rel}")
                if setup(wt,strategy,m,mp,repo,rd): commit_setup(wt)
                prompt="\n\n".join(x.strip() for x in [strategy.get("prompt_prefix",""),task.get("user_prompt","")] if x.strip())
                cmd=["codex","exec","--json","-C",str(wt)]
                if strategy.get("model"): cmd += ["-m",strategy["model"]]
                if strategy.get("sandbox","workspace-write"): cmd += ["-s",strategy.get("sandbox","workspace-write")]
                for ov in strategy.get("config_overrides",[]): cmd += ["-c",str(ov)]
                cmd.append(prompt); (rd/"command.json").write_text(json.dumps(cmd,indent=2),encoding="utf-8"); (rd/"prompt.txt").write_text(prompt,encoding="utf-8")
                j=rd/"codex.jsonl"; e=rd/"codex.stderr.txt"; started=time.monotonic()
                with j.open("w",encoding="utf-8") as jo,e.open("w",encoding="utf-8") as er:
                    try: rc=run(cmd,timeout=int(task.get("max_wall_seconds",m.get("max_wall_seconds",1800))),stdout=jo,stderr=er).returncode
                    except subprocess.TimeoutExpired: rc=124
                wall=time.monotonic()-started; u=usage_from_jsonl(j); pcost=principal_cost(u,ratio); scost=system_cost(u,pcost,strategy)
                try: aok,ar=check_commands(wt,task.get("acceptance",[]),int(task.get("acceptance_timeout_seconds",900)))
                except subprocess.TimeoutExpired: aok,ar=False,[{"error":"acceptance timeout"}]
                try: rok,rr=check_commands(wt,task.get("regression",[]),int(task.get("acceptance_timeout_seconds",900)))
                except subprocess.TimeoutExpired: rok,rr=False,[{"error":"regression timeout"}]
                (rd/"acceptance.json").write_text(json.dumps(ar,indent=2),encoding="utf-8"); (rd/"regression.json").write_text(json.dumps(rr,indent=2),encoding="utf-8")
                diff=git(wt,"diff","--binary"); (rd/"patch.diff").write_text(diff.stdout,encoding="utf-8",errors="replace")
                row={"task":task["id"],"strategy":strategy["name"],"repetition":rep,"valid_success":rc==0 and aok and rok,
                     "codex_exit_code":rc,"acceptance_ok":aok,"regression_ok":rok,"wall_seconds":wall,
                     "principal_cost_equiv":pcost,"system_cost_equiv":scost,**u}; rows.append(row)
                with (out/"runs.csv").open("w",newline="",encoding="utf-8") as f:
                    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
                (out/"runs.json").write_text(json.dumps(rows,indent=2),encoding="utf-8"); (out/"aggregate.json").write_text(json.dumps(aggregate(rows),indent=2),encoding="utf-8"); summary(out/"summary.md",rows)
                print(f"[{n}/{len(plan)}] {row['task']} {row['strategy']} success={row['valid_success']} input={u['input_tokens']} worker_calls={u['worker_calls']}")
            finally: git(repo,"worktree","remove","--force",str(wt),timeout=180)
    finally: shutil.rmtree(temp,ignore_errors=True); git(repo,"worktree","prune")
    print(json.dumps(aggregate(rows),indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
