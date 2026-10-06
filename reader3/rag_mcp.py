"""MCP stdio server with the book tools for headless Claude (see notebook_api.agent_stream).

It stays tiny on purpose: no models, no torch. Every tool calls the running reader server (/internal/nb/*) over localhost,
so the embedder and the reranker stay warm on the GPU and the server decides which books are in scope.
Environment: NB_URL (http://127.0.0.1:8123), NB_TOKEN, NB_BOOKS ("bookid:A,bookid2:B")."""
import json
import os
import urllib.request

try:
    from mcp.server.mcpserver import MCPServer as FastMCP      # mcp 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP                       # mcp 1.x

mcp = FastMCP("book")
URL = os.environ.get("NB_URL", "http://127.0.0.1:8123").rstrip("/")
TOKEN = os.environ.get("NB_TOKEN", "")
BOOKS = os.environ.get("NB_BOOKS", "")


def call(op: str, **payload) -> str:
    body = json.dumps({"books": BOOKS, **payload}).encode("utf-8")
    req = urllib.request.Request(f"{URL}/internal/nb/{op}", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-NB-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8")).get("text", "")
    except Exception as e:                       # the model sees a plain message and can retry or answer without the tool
        return f"Ошибка инструмента: {e}"


@mcp.tool()
def list_sources() -> str:
    """List the books (sources) of this notebook: letter, title, format, language, size. Use the letters in the other tools."""
    return call("sources")


@mcp.tool()
def outline(source: str) -> str:
    """Table of contents of one source (letter A, B, ...), with page numbers (PDF) or chapter numbers (EPUB)."""
    return call("outline", source=source)


@mcp.tool()
def search(query: str, source: str = "", k: int = 6) -> str:
    """Search the books for passages (hybrid keyword + semantic search, reranked). Returns passages tagged like [A:41] with
    a relevance score 0..1. Write the query in the language of the BOOK, as 4-10 keywords. Use several different queries.
    source: optional letter to search only one book. k: how many passages (1-12)."""
    return call("search", query=query, source=source, k=k)


@mcp.tool()
def read(source: str, start: int, end: int = 0) -> str:
    """Read pages (PDF) or chapters (EPUB) start..end of one source in full (limit about 14000 characters per call)."""
    return call("read", source=source, start=start, end=end)


@mcp.tool()
def find_exact(phrase: str, source: str) -> str:
    """Find where an exact phrase occurs in a source (page/chapter numbers). Use it to check a quotation or a rare term."""
    return call("find", phrase=phrase, source=source)


@mcp.tool()
def summary(source: str, section: str = "") -> str:
    """Pre-written summaries of the chapters of a source (use for overview questions: what is the book about, main themes,
    compare chapters). section: optional part of a chapter title to filter. Summaries simplify: quote from the original."""
    return call("summary", source=source, section=section)


if __name__ == "__main__":
    mcp.run()
