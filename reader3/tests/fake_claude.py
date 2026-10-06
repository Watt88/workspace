"""A stand-in for `claude -p ... --output-format stream-json`: behaviour is chosen by FAKE_CLAUDE_SCENARIO."""
import json
import os
import sys
import time

scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "ok")
args = sys.argv[1:]
stdin = sys.stdin.read()


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def arg(name):
    return args[args.index(name) + 1] if name in args else None


def delta(text):
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})


def init(status="connected"):
    out({"type": "system", "subtype": "init", "mcp_servers": [{"name": "book", "status": status}]})


if scenario == "crash":
    sys.stderr.write("fatal: not logged in\n")
    sys.exit(1)
if scenario == "hang":
    init()
    time.sleep(30)
elif scenario == "mcp_fail":
    init("failed")
    delta("без инструментов")
elif scenario == "is_error":
    init()
    out({"type": "result", "is_error": True, "result": "rate limit"})
elif scenario == "garbage":
    sys.stdout.write("not json at all\n\n{broken\n")
    init()
    delta("при")
    sys.stdout.write("\n")
    delta("вет")
elif scenario == "echo":
    init()
    info = {"args": args, "stdin": stdin}
    for key in ("--mcp-config", "--system-prompt-file"):
        p = arg(key)
        if p and os.path.exists(p):
            info[key] = open(p, encoding="utf-8").read()
    delta(json.dumps(info, ensure_ascii=False))
else:                                                          # "ok": preface, a tool call, then the answer
    init()
    delta("Сейчас поищу. ")
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__book__search", "input": {"query": "q", "source": "A"}},
                                                      {"type": "text", "text": "ignored"}]}})
    delta("Ответ ")
    delta("готов.")
    out({"type": "result", "is_error": False, "result": "Ответ готов."})
