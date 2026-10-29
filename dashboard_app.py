"""Interactive dashboard: streamlit run dashboard_app.py"""
import glob
import os
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from openpyxl import load_workbook

from recon import server
from recon.config import Settings
from recon.costs import LOCAL, PRICING, cost, scan_inputs
from recon.events import read_events
from recon.runner import STEPS, RunController

st.set_page_config(page_title="Statement Reconciliation", page_icon="📊", layout="wide")


@st.cache_resource
def controller() -> RunController:
    return RunController()


ctl = controller()
OUT = Settings().output_folder
RUNS = os.path.join(OUT, "runs")


@st.cache_data(ttl=10)
def cached_scan(folder: str) -> dict:
    return scan_inputs(folder)


@st.cache_data(ttl=15)
def cached_server_status() -> dict:
    return server.status()


def run_files() -> list[str]:
    return sorted(glob.glob(os.path.join(RUNS, "*.jsonl")), reverse=True)


def summarize(evs: list[dict]) -> dict:
    """Fold the event stream into the state the UI renders."""
    s = {"steps": {}, "current": None, "logs": [], "docs": [], "disc": [], "metrics": {}, "progress": None,
         "status": "idle", "result": "", "start": None, "end": None, "plan": None, "server": {},
         "client_files": [], "trackers": [], "reports": None}
    for e in evs:
        k = e["kind"]
        if k == "run_start":
            s["status"], s["start"] = "running", e["ts"]
        elif k == "step":
            if s["current"] is not None:
                s["steps"][s["current"]]["done"] = e["ts"]
            s["current"] = e["n"]
            s["steps"][e["n"]] = {"title": e["title"], "start": e["ts"], "done": None}
        elif k == "log":
            s["logs"].append(f"{time.strftime('%H:%M:%S', time.localtime(e['ts']))} {e['level']:<7} {e['msg']}")
        elif k == "doc":
            s["docs"].append(e)
        elif k == "progress":
            s["progress"] = e
        elif k == "discrepancies":
            s["disc"] = e["rows"]
        elif k == "metrics":
            s["metrics"] = e
        elif k == "plan":
            s["plan"] = e
        elif k == "server":
            s["server"] = e
        elif k == "client_file":
            s["client_files"].append(e)
        elif k == "tracker":
            s["trackers"].append(e)
        elif k == "reports":
            s["reports"] = e
        elif k == "run_end":
            s["status"], s["result"], s["end"] = e["status"], e["result"], e["ts"]
            if s["current"] is not None:
                s["steps"][s["current"]]["done"] = e["ts"]
    return s


def file_link(path: str) -> str:
    return f"[{path}]({Path(path).resolve().as_uri()})"


def doc_frame(s: dict, pricing: str) -> pd.DataFrame:
    """One row per planned PDF (PENDING/RUNNING until its result event arrives), with cost for the chosen pricing."""
    done = {d["file"]: d for d in s["docs"]}
    names = [d["file"] for d in (s["plan"] or {}).get("docs", [])] or list(done)
    cur = s["progress"]["current"] if s["progress"] and s["status"] == "running" else None
    rows = []
    for i, name in enumerate(names, 1):
        d = done.get(name)
        if d is None:
            rows.append({"#": i, "File": name, "Status": "⏳ RUNNING" if name == cur else "⬜ PENDING"})
            continue
        rows.append({
            "#": i, "File": name, "Status": "✅ OK" if d["status"] == "OK" else "❌ FAILED", "Pages": d["pages"],
            "Tries": d["attempts"], "Tokens in": d["prompt_tokens"], "Tokens out": d["completion_tokens"],
            "Read (s)": round(d["read_secs"], 2), "LLM (s)": round(d["llm_secs"], 1),
            "Total (s)": round(d["total_secs"], 1), "Statements": d["statements"], "Trades": d["trades"],
            f"Cost ($) [{pricing}]": round(cost(d["prompt_tokens"], d["completion_tokens"], pricing), 4),
            "Notes": d["error"] or ", ".join(d["accounts"]),
        })
    return pd.DataFrame(rows)


def stepper(s: dict) -> None:
    cols = st.columns(len(STEPS))
    for n, col in zip(STEPS, cols):
        info = s["steps"].get(n)
        if info is None:
            icon, note = "⬜", "pending"
        elif info["done"] is None:
            icon, note = "⏳", f"{time.time() - info['start']:.0f}s…"
        elif s["status"] in ("failed", "cancelled") and n == s["current"]:
            icon, note = "❌", f"{info['done'] - info['start']:.1f}s"
        else:
            icon, note = "✅", f"{info['done'] - info['start']:.1f}s"
        col.markdown(f"**{icon} {n}. {STEPS[n]}**  \n{note}")


def prerun_panel(scan: dict, title: str) -> None:
    docs = scan["docs"]
    pages = sum(d["pages"] or 0 for d in docs)
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.info(f"📄 **{len(docs)}** PDF(s) to scan ({pages} pages) · 📑 {len(scan['client_files'])} client trade "
                f"file(s) · 🗂 {len(scan['master_files'])} master tracker(s)")
        c1, c2 = st.columns([3, 2])
        c1.dataframe(pd.DataFrame([{"Check": n, "Status": "✅" if ok else "⚠️", "Detail": d}
                                   for n, ok, d in scan["checks"]]), hide_index=True, width="stretch")
        if docs:
            c2.dataframe(pd.DataFrame(docs), hide_index=True, width="stretch")


def download_buttons(paths: list[str], key: str) -> None:
    cols = st.columns([1, 1, 6])
    for col, path in zip(cols, paths):
        if os.path.exists(path):
            with open(path, "rb") as fh:
                col.download_button(f"⬇ {os.path.splitext(path)[1][1:].upper()}", fh.read(),
                                    os.path.basename(path), key=f"{key}_{path}")


def result_row(s: dict, run_id: str) -> None:
    if s["status"] == "failed":
        st.error(s["result"])
        return
    lines = [ln for ln in s["result"].splitlines() if not ln.startswith(("Excel:", "PDF:"))]
    with st.container(border=True):
        st.markdown("**Result:** " + " ".join(lines))
        rep = s["reports"]
        if rep:
            st.markdown(f"**Excel:** {file_link(rep['excel'])}  \n**PDF:** {file_link(rep['pdf'])}")
            download_buttons([rep["excel"], rep["pdf"]], f"res_{run_id}")


def live_view(path: str | None, was_running: bool, pricing: str, folder: str) -> None:
    s = summarize(read_events(path)) if path else summarize([])
    badge = {"idle": "⚪ Idle", "running": "🟢 Running", "done": "✅ Finished", "failed": "🔴 Failed",
             "cancelled": "🟠 Cancelled"}[s["status"]]
    elapsed = ((s["end"] or time.time()) - s["start"]) if s["start"] else 0
    run_id = os.path.basename(path)[:-6] if path else ""
    st.subheader(f"{badge}  ·  {elapsed:.0f}s" + (f"  ·  run {run_id}" if path else ""))

    if s["plan"]:
        prerun_panel(s["plan"], "Pre-run summary (captured at run start)")
    elif not ctl.running:
        prerun_panel(cached_scan(folder), "Pre-run check (current docs folder)")

    stepper(s)
    p = s["progress"]
    if p and s["current"] == 1 and s["status"] == "running":
        st.progress(p["done"] / max(p["total"], 1), text=f"Extracting {p['current']} ({p['done']}/{p['total']} PDFs done)")
    m = s["metrics"]
    if m:
        for col, (label, key) in zip(st.columns(5), [("Statements", "statements"), ("Advisor trades", "advisor_trades"),
                                                     ("Client trades", "client_trades"), ("Discrepancies", "discrepancies"),
                                                     ("Suspicious", "suspicious")]):
            col.metric(label, m[key])

    # Step details appear as each step completes
    df = doc_frame(s, pricing)
    if not df.empty:
        st.markdown("##### Step 1 · Statement extraction")
        st.dataframe(df, hide_index=True, width="stretch")
        docs = s["docs"]
        st.caption(f"{sum(d['trades'] for d in docs)} trades · "
                   f"{sum(d['prompt_tokens'] + d['completion_tokens'] for d in docs):,} tokens · est. cost "
                   f"${sum(cost(d['prompt_tokens'], d['completion_tokens'], pricing) for d in docs):.4f} ({pricing})")
    if s["client_files"]:
        st.markdown("##### Step 2 · Client trades loaded")
        st.dataframe(pd.DataFrame(s["client_files"]).drop(columns=["kind", "ts"]), hide_index=True, width="stretch")
    if s["disc"]:
        st.markdown("##### Step 3 · Discrepancies")
        st.dataframe(pd.DataFrame(s["disc"]), hide_index=True, width="stretch")
    if s["trackers"]:
        st.markdown("##### Step 4 · MasterPro trackers updated")
        st.dataframe(pd.DataFrame(s["trackers"]).drop(columns=["kind", "ts"]), hide_index=True, width="stretch")

    if s["result"]:
        result_row(s, run_id)

    with st.expander(f"Logs ({len(s['logs'])} lines)", expanded=False):
        st.code("\n".join(s["logs"][-200:]) or "No log yet", language="text", height=220)

    if was_running and not ctl.running:
        st.rerun()  # run finished: refresh the other tabs and the sidebar


def live_tab(path: str | None, pricing: str, folder: str) -> None:
    was_running = ctl.running
    st.fragment(run_every=1 if was_running else None)(lambda: live_view(path, was_running, pricing, folder))()


def metrics_tab(s: dict, pricing: str) -> None:
    if not s["docs"]:
        st.info("No extraction metrics for this run yet.")
        return
    df = doc_frame(s, pricing)
    docs = pd.DataFrame(s["docs"])
    pin, pout = int(docs.prompt_tokens.sum()), int(docs.completion_tokens.sum())
    st.caption(f"Run model: **{s['server'].get('model', 'unknown')}** (local Foundry, no per-token charge). "
               f"Cost columns use the pricing selected in the sidebar: **{pricing}**.")
    a, b, c, d, e = st.columns(5)
    a.metric("PDFs OK", f"{(docs.status == 'OK').sum()}/{len(docs)}")
    b.metric("Tokens in / out", f"{pin:,} / {pout:,}")
    c.metric("LLM time", f"{docs.llm_secs.sum():.1f}s")
    d.metric("Retries", int((docs.attempts - 1).clip(lower=0).sum()))
    e.metric(f"Est. cost ({pricing})", f"${cost(pin, pout, pricing):.4f}")
    st.dataframe(df, hide_index=True, width="stretch")
    st.markdown("##### Cost if this run used a different model")
    st.dataframe(pd.DataFrame([{"Model": n, "$/1M in": pr[0], "$/1M out": pr[1],
                                "Est. cost for this run ($)": round(cost(pin, pout, n), 4)}
                               for n, pr in PRICING.items()]), hide_index=True, width="stretch")
    st.caption("Prices are approximate list prices (edit `recon/costs.py`). Local models cost $0 per token.")
    left, right = st.columns(2)
    left.plotly_chart(px.bar(docs, x="file", y=["read_secs", "llm_secs"], title="Time per PDF (s)"), width="stretch")
    right.plotly_chart(px.bar(docs, x="file", y=["prompt_tokens", "completion_tokens"], title="Tokens per PDF"),
                       width="stretch")


def discrepancies_tab(s: dict) -> None:
    if not s["disc"]:
        st.info("No discrepancies recorded for this run.")
        return
    df = pd.DataFrame(s["disc"])
    f1, f2 = st.columns(2)
    kinds = f1.multiselect("Kind", sorted(df.kind.unique()), default=sorted(df.kind.unique()))
    accts = f2.multiselect("Account", sorted(df.account.unique()), default=sorted(df.account.unique()))
    view = df[df.kind.isin(kinds) & df.account.isin(accts)]
    st.dataframe(view.style.apply(lambda r: ["background-color:#5c1f1f" if r.kind == "PRICE" else "" for _ in r], axis=1),
                 width="stretch", hide_index=True)
    st.plotly_chart(px.histogram(view, x="kind", color="account", title="Discrepancies by kind"), width="stretch")
    st.download_button("Download CSV", view.to_csv(index=False), "discrepancies.csv")


@st.cache_data
def read_report(path: str, mtime: float) -> dict:
    wb = load_workbook(path, read_only=True, data_only=True)
    summary = {str(r[0]): r[1] for r in wb["Summary"].iter_rows(values_only=True) if r and r[0] is not None}

    def sheet(name: str) -> pd.DataFrame:
        rows = list(wb[name].iter_rows(values_only=True))
        return pd.DataFrame(rows[1:], columns=rows[0]) if rows else pd.DataFrame()

    return {"summary": summary, "discrepancies": sheet("Discrepancies"), "unmatched": sheet("Unmatched")}


def reports_tab() -> None:
    stems = sorted({os.path.splitext(f)[0] for f in glob.glob(os.path.join(OUT, "suspicious_activity_report_*"))},
                   reverse=True)
    if not stems:
        st.info("No reports generated yet.")
        return
    totals = ("Client trades", "Advisor trades", "Total discrepancies")
    rows = []
    for stem in stems:
        xlsx, pdf = stem + ".xlsx", stem + ".pdf"
        info = read_report(xlsx, os.path.getmtime(xlsx))["summary"] if os.path.exists(xlsx) else {}
        kinds = {k: v for k, v in info.items() if k not in totals}
        ts = os.path.basename(stem).rsplit("_", 2)
        rows.append({
            "Generated": pd.to_datetime(f"{ts[-2]}{ts[-1]}", format="%Y%m%d%H%M%S", errors="coerce"),
            "Client trades": info.get("Client trades"), "Advisor trades": info.get("Advisor trades"),
            "Discrepancies": info.get("Total discrepancies"), "Suspicious (PRICE)": kinds.get("PRICE", 0),
            "By kind": ", ".join(f"{k}: {v}" for k, v in kinds.items()),
            "Excel (KB)": round(os.path.getsize(xlsx) / 1024, 1) if os.path.exists(xlsx) else None,
            "PDF (KB)": round(os.path.getsize(pdf) / 1024, 1) if os.path.exists(pdf) else None,
            "Excel": os.path.abspath(xlsx), "PDF": os.path.abspath(pdf),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    pick = st.selectbox("Open report", stems,
                        format_func=lambda x: os.path.basename(x).replace("suspicious_activity_report_", ""))
    xlsx, pdf = pick + ".xlsx", pick + ".pdf"
    st.markdown(f"**Excel:** {file_link(xlsx)}  \n**PDF:** {file_link(pdf)}")
    download_buttons([xlsx, pdf], "rep")
    if os.path.exists(xlsx):
        data = read_report(xlsx, os.path.getmtime(xlsx))
        st.markdown("##### Discrepancies")
        st.dataframe(data["discrepancies"], hide_index=True, width="stretch")
        st.markdown("##### Unmatched trades")
        st.dataframe(data["unmatched"], hide_index=True, width="stretch")


def history_tab(pricing: str) -> None:
    rows = []
    for f in run_files():
        s = summarize(read_events(f))
        pin = sum(d["prompt_tokens"] for d in s["docs"])
        pout = sum(d["completion_tokens"] for d in s["docs"])
        rows.append({
            "Run": os.path.basename(f)[:-6],
            "Started": pd.to_datetime(s["start"], unit="s") if s["start"] else None,
            "Status": s["status"], "Model": s["server"].get("model", ""),
            "Duration (s)": round(s["end"] - s["start"], 1) if s["end"] and s["start"] else None,
            "PDFs": len(s["docs"]), "Tokens": pin + pout, f"Cost ($) [{pricing}]": round(cost(pin, pout, pricing), 4),
            "Statements": s["metrics"].get("statements"), "Discrepancies": s["metrics"].get("discrepancies"),
            "Suspicious": s["metrics"].get("suspicious"),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("No runs yet.")


def models_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Alias": m.get("alias"), "Id": m.get("id"), "Type": m.get("type"),
                          "Device": m.get("device"), "Size (MB)": m.get("fileSizeMb"),
                          "Tool calling": m.get("supportsToolCalling"), "License": m.get("license")} for m in rows])


def server_tab() -> None:
    busy = ctl.running
    c = st.columns([1, 1, 1, 1, 3])
    action = None
    if c[0].button("▶ Start", disabled=busy, width="stretch"):
        action = ("Starting server and loading model (this can take a while)...", server.start)
    if c[1].button("⏹ Stop", disabled=busy, width="stretch"):
        action = ("Stopping server...", server.stop)
    if c[2].button("🔄 Restart", disabled=busy, width="stretch"):
        action = ("Restarting server...", server.restart)
    if c[3].button("↻ Refresh", width="stretch"):
        cached_server_status.clear()
    if action:
        with st.spinner(action[0]):
            ok, msg = action[1]()
        cached_server_status.clear()
        st.session_state["server_msg"] = (ok, msg)
    if busy:
        st.caption("Server controls are disabled while a run is in progress.")
    if "server_msg" in st.session_state:
        ok, msg = st.session_state["server_msg"]
        (st.success if ok else st.error)(msg)

    with st.spinner("Reading server status..."):
        sv = cached_server_status()
    k = st.columns(4)
    k[0].metric("State", sv["state"] or "unknown")
    k[1].metric("API", "reachable" if sv["api_ok"] else "down")
    k[2].metric("Model served", "yes" if sv["model_served"] else "no")
    k[3].metric("API latency", f"{sv['latency_ms']:.0f} ms" if sv["latency_ms"] is not None else "-")
    if not sv["port_match"]:
        st.warning("FOUNDRY_HOST port differs from the running server. Use Restart to refresh it.")

    st.markdown("##### Server details")
    endpoint = sv["env_host"] or (sv["urls"][0] if sv["urls"] else "-")
    st.dataframe(pd.DataFrame([
        ("Foundry CLI", sv["cli_version"]),
        ("Process running", "yes" if sv["running"] else "no"),
        ("State", sv["state"] or "unknown"),
        ("Server URL(s)", ", ".join(sv["urls"]) or "-"),
        ("FOUNDRY_HOST (env)", sv["env_host"] or "not set"),
        ("FOUNDRY_MODEL (env)", sv["env_model"] or "not set"),
        ("Port matches running server", "yes" if sv["port_match"] else "NO"),
        ("OpenAI-compatible endpoint", f"{endpoint}/v1"),
        ("Models in API catalog", len(sv["served"])),
        ("Loaded models", len(sv["loaded"])),
        ("Cached models", len(sv["cached"])),
        ("Last checked", time.strftime("%H:%M:%S", time.localtime(sv["checked_at"]))),
    ], columns=["Property", "Value"]).astype(str), hide_index=True, width="stretch")

    st.markdown("##### Loaded models")
    if sv["loaded"]:
        st.dataframe(models_frame(sv["loaded"]), hide_index=True, width="stretch")
    else:
        st.caption("None loaded.")
    st.markdown("##### Cached models")
    if sv["cached"]:
        st.dataframe(models_frame(sv["cached"]), hide_index=True, width="stretch")
    else:
        st.caption("None cached.")


with st.sidebar:
    st.title("📊 Reconciliation")
    folder = st.text_input("Docs folder", Settings().docs_folder)
    dry = st.toggle("Dry run (no email)", True)
    in_place = st.toggle("Update master trackers in place", False)
    keep = st.toggle("Keep Foundry server running", True)
    pricing = st.selectbox("Cost model", list(PRICING), index=list(PRICING).index(LOCAL),
                           help="Token pricing used for cost estimates. The run itself uses the local Foundry model.")
    scan = cached_scan(folder)
    if not ctl.running:
        if st.button("▶ Run reconciliation", type="primary", width="stretch", disabled=not scan["blocking_ok"]):
            cfg = Settings()
            cfg.docs_folder, cfg.in_place = folder, in_place
            ctl.start(cfg, dry, keep)
            st.rerun()
        if not scan["blocking_ok"]:
            st.caption("⚠️ Need statements, client trades and master trackers in the docs folder.")
    else:
        st.button("⏳ Running…", disabled=True, width="stretch")
        if st.button("⏹ Cancel", width="stretch"):
            ctl.cancel()
    st.divider()
    files = run_files()
    live_id = os.path.join(RUNS, f"{ctl.run_id}.jsonl") if ctl.running else None
    choice = st.selectbox("Run to view", files, format_func=lambda f: os.path.basename(f)[:-6]) if files else None
    selected = live_id or choice

tabs = st.tabs(["🔴 Live", "📈 Metrics", "⚠️ Discrepancies", "📄 Reports", "🕘 History", "🖥 Server"])
state = summarize(read_events(selected)) if selected else summarize([])
with tabs[0]:
    live_tab(selected, pricing, folder)
with tabs[1]:
    metrics_tab(state, pricing)
with tabs[2]:
    discrepancies_tab(state)
with tabs[3]:
    reports_tab()
with tabs[4]:
    history_tab(pricing)
with tabs[5]:
    server_tab()
