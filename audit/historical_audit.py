"""Temporary read-only audit of Claude Code transcripts -> sessions.csv, turns.csv."""
import csv, glob, json, os, sys
from collections import Counter
from datetime import datetime

ROOT = os.path.expanduser(r"~\.claude\projects")
OUT = os.path.dirname(os.path.abspath(__file__))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def prompt_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c if isinstance(b, dict)):
            return None
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return None


sessions, turn_rows = [], []
for path in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
    rel = os.path.relpath(path, ROOT)
    is_sub = os.sep + "subagents" + os.sep in path
    turns = {}  # message.id -> dict (dedup of streamed / split lines)
    order = []
    sid = cwd = None
    models = Counter()
    times = []
    prompts, first_prompt, compactions = 0, "", 0
    tools, read_files = Counter(), Counter()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            sid = sid or e.get("sessionId")
            cwd = cwd or e.get("cwd")
            if e.get("timestamp"):
                times.append(ts(e["timestamp"]))
            if e.get("isCompactSummary") or e.get("subtype") == "compact_boundary":
                compactions += 1
            t = e.get("type")
            msg = e.get("message") or {}
            if t == "user" and not e.get("isMeta") and not e.get("isCompactSummary"):
                txt = prompt_text(msg)
                if txt and not txt.lstrip().startswith(("<local-command", "<command-name>/clear", "Caveat:")):
                    prompts += 1
                    first_prompt = first_prompt or " ".join(txt.split())[:160]
            if t != "assistant" or not msg.get("usage"):
                continue
            mid = msg.get("id") or e.get("requestId") or e.get("uuid")
            u = msg["usage"]
            if mid not in turns:
                turns[mid] = {"ts": e.get("timestamp"), "model": msg.get("model", ""), "tools": []}
                order.append(mid)
            rec = turns[mid]
            # streamed chunks repeat usage; keep the max of each field (final chunk carries totals)
            for k, src in (("in", "input_tokens"), ("out", "output_tokens"),
                           ("cw", "cache_creation_input_tokens"), ("cr", "cache_read_input_tokens")):
                rec[k] = max(rec.get(k, 0), u.get(src) or 0)
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in rec.setdefault("ids", set()):
                    rec["ids"].add(b.get("id"))
                    name = b.get("name", "?")
                    rec["tools"].append(name)
                    tools[name] += 1
                    inp = b.get("input") or {}
                    if name == "Read" and inp.get("file_path"):
                        read_files[inp["file_path"].lower()] += 1
    real = [m for m in order if turns[m]["model"] != "<synthetic>"]
    if not real and not prompts:
        continue
    tot = {k: sum(turns[m].get(k, 0) for m in real) for k in ("in", "out", "cw", "cr")}
    total = sum(tot.values())
    best = None
    for i, m in enumerate(real, 1):
        r = turns[m]
        tt = r["in"] + r["out"] + r["cw"] + r["cr"]
        models[r["model"]] += 1
        if best is None or tt > best[1]:
            best = (i, tt)
        turn_rows.append({"session_id": sid, "file": rel, "turn": i, "ts": r["ts"], "model": r["model"],
                          "input": r["in"], "output": r["out"], "cache_write": r["cw"], "cache_read": r["cr"],
                          "total": tt, "context": r["in"] + r["cw"] + r["cr"], "tools": ";".join(r["tools"])})
    start, end = (min(times), max(times)) if times else (None, None)
    n = len(real)
    sessions.append({
        "session_id": sid, "file": rel, "is_subagent": is_sub, "cwd": cwd,
        "model": ";".join(f"{k}:{v}" for k, v in models.most_common()),
        "start": start.isoformat() if start else "", "end": end.isoformat() if end else "",
        "duration_min": round((end - start).total_seconds() / 60, 1) if start else "",
        "user_prompts": prompts, "assistant_turns": n,
        "input": tot["in"], "output": tot["out"], "cache_write": tot["cw"], "cache_read": tot["cr"],
        "total": total, "avg_per_turn": round(total / n) if n else 0,
        "max_turn_idx": best[0] if best else "", "max_turn_tokens": best[1] if best else 0,
        "tool_calls": sum(tools.values()), "read": tools["Read"], "grep": tools["Grep"] + tools["Glob"],
        "bash": tools["Bash"] + tools["PowerShell"], "edit": tools["Edit"] + tools["Write"],
        "agent": tools["Agent"] + tools["Task"],
        "repeated_reads": sum(c - 1 for c in read_files.values() if c > 1),
        "distinct_files_read": len(read_files), "compactions": compactions,
        "top_tools": ";".join(f"{k}:{v}" for k, v in tools.most_common(6)),
        "first_prompt": first_prompt,
    })

# attribute subagent files to parent session
for s in sessions:
    s["subagent_count"] = sum(1 for x in sessions if x["is_subagent"] and s["session_id"] and
                              x["file"].startswith(os.path.dirname(s["file"]) + os.sep + s["session_id"]))
sessions.sort(key=lambda s: s["total"], reverse=True)
for name, rows in (("sessions.csv", sessions), ("turns.csv", turn_rows)):
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
print(len(sessions), "sessions,", len(turn_rows), "turns ->", OUT)
