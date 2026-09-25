# Historical usage audit

Standalone, read-only audit of past Claude Code sessions. Not used by
`claude_monitor.py` / `claude_monitor_gui.py`.

## Files

| File | Contents |
|---|---|
| `historical_audit.py` | Parser. Reads every `*.jsonl` transcript under `~/.claude/projects/` (including `subagents/`) and writes the two CSVs below. |
| `sessions.csv` | One row per transcript, sorted by total tokens: session ID, file, sub-agent flag, cwd, model(s), start/end, duration, user prompts, assistant turns, input / output / cache-write / cache-read / total tokens, average and max tokens per turn, tool-call counts (Read, Grep+Glob, Bash+PowerShell, Edit+Write, Agent), repeated Read count, distinct files read, compactions, top tools, sub-agent count, first-prompt snippet. |
| `turns.csv` | One row per assistant turn: session ID, file, turn number, timestamp, model, input / output / cache-write / cache-read / total tokens, `context` (input + cache write + cache read, i.e. history size at that turn), tools called. |

Streamed assistant records that share a `message.id` are merged into one turn
(max of each usage field), so tokens are not double-counted. `<synthetic>`
turns are excluded.

## Rerun

```
python audit/historical_audit.py
```

Outputs are written next to the script, overwriting the existing CSVs.
Both CSVs are git-ignored and stay local only.
Requires only the Python standard library.

## Safety

Source transcripts in `~/.claude/projects/` are opened read-only and never
modified. Note that `sessions.csv` contains working directories and the first
160 characters of each session's first prompt — review before publishing.
