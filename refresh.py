# -*- coding: utf-8 -*-
"""
SanDisk (SNDK) 做空择时评分卡 — 每日刷新:抓数据 -> 算分 -> 追加历史 -> 生成 index.html + status.json
本地: python refresh.py    云端: GitHub Actions cron
auto 层只用 yfinance(可靠);基本面层走 state.json 手填。镜像 korea-scorecard 的容错/熔断/sticky 守卫。
"""
import os, json, csv, sys, html, math
from datetime import datetime, timezone, timedelta
import config as C

ET = timezone(timedelta(hours=-4))   # 美东(近似 EDT,仅用于时间戳显示)
HERE = os.path.dirname(os.path.abspath(__file__))
def P(*a): return os.path.join(HERE, *a)

def safe(fn, what, default=None):
    try:
        return fn()
    except Exception as e:
        print(f"[warn] {what}: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return default

# ---------- 技术指标(自算,不加依赖) ----------
def _rsi(closes, n=14):
    if len(closes) < n + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n; al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
    if al == 0: return 100.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)

def _ema(closes, n):
    if len(closes) < n: return None
    k = 2 / (n + 1); e = sum(closes[:n]) / n
    for c in closes[n:]:
        e = c * k + e * (1 - k)
    return round(e, 2)

def _sma(closes, n):
    if len(closes) < n: return None
    return round(sum(closes[-n:]) / n, 2)

def _ret5(closes):
    if len(closes) < 6: return None
    return round((closes[-1] / closes[-6] - 1) * 100, 1)

# ---------- 数据抓取(yfinance) ----------
def _closes(tk, period="6mo"):
    import yfinance as yf
    h = yf.Ticker(tk).history(period=period)["Close"].dropna()
    return [float(x) for x in h.tolist()], (h.index[-1].date().isoformat() if len(h) else None)

def fetch_market():
    m = {}
    sn = safe(lambda: _closes("SNDK"), "SNDK")
    if not sn or not sn[0]:
        return m  # 行情挂 → 上层熔断保留旧页
    closes, ddate = sn
    m["date"] = ddate
    m["sndk"] = round(closes[-1], 2)
    m["sndk_prev"] = round(closes[-2], 2) if len(closes) > 1 else None
    if m["sndk_prev"]:
        m["sndk_chg"] = round((m["sndk"] / m["sndk_prev"] - 1) * 100, 2)
    m["rsi"] = _rsi(closes)
    m["ema21"] = _ema(closes, 21)
    m["sma50"] = _sma(closes, 50)
    m["sndk_5d"] = _ret5(closes)
    # 同业 5 日
    def r5(tk):
        v = safe(lambda: _closes(tk, "1mo"), tk)
        return _ret5(v[0]) if v and v[0] else None
    m["mu_5d"] = r5("MU"); m["wdc_5d"] = r5("WDC")
    peers = [x for x in (m["mu_5d"], m["wdc_5d"]) if x is not None]
    m["peer_5d"] = round(sum(peers) / len(peers), 1) if peers else None
    m["crwv_5d"] = r5("CRWV"); m["orcl_5d"] = r5("ORCL")
    nc = [x for x in (m["crwv_5d"], m["orcl_5d"]) if x is not None]
    m["neocloud_5d"] = round(sum(nc) / len(nc), 1) if nc else None
    # 韩股隔夜(最新一日 %)
    def chg1(tk):
        v = safe(lambda: _closes(tk, "10d"), tk)
        if v and v[0] and len(v[0]) > 1:
            return round((v[0][-1] / v[0][-2] - 1) * 100, 2)
        return None
    m["hynix_chg"] = chg1("000660.KS"); m["samsung_chg"] = chg1("005930.KS")
    ov = [x for x in (m["hynix_chg"], m["samsung_chg"]) if x is not None]
    m["overnight"] = round(sum(ov) / len(ov), 2) if ov else None
    # IV30(best-effort:最近到期 ATM 隐含波动)
    m["iv30"] = safe(fetch_iv, "IV30")
    return m

def fetch_iv():
    import yfinance as yf
    t = yf.Ticker("SNDK")
    px = float(t.history(period="1d")["Close"].iloc[-1])
    exps = t.options
    if not exps: return None
    oc = t.option_chain(exps[0])
    ivs = []
    for df in (oc.calls, oc.puts):
        if df is None or not len(df): continue
        df = df.assign(_d=(df["strike"] - px).abs()).sort_values("_d").head(3)
        ivs += [float(x) for x in df["impliedVolatility"].tolist() if x and math.isfinite(float(x))]
    if not ivs: return None
    val = sum(ivs) / len(ivs) * 100
    return round(val, 1) if 5 <= val <= 400 else None   # yfinance IV 常返 ~0 的脏值 → 范围外弃用,回落 state.iv30

# ---------- 算分 ----------
def auto_score(ind, m, state):
    a = ind.get("auto")
    if a == "tech":     return C.score_tech(m.get("sndk"), m.get("ema21"), m.get("sma50"), m.get("rsi"))
    if a == "rs_peer":  return C.score_rs(m.get("sndk_5d"), m.get("peer_5d"))
    if a == "overnight":return C.score_overnight(m.get("overnight"))
    if a == "neocloud": return C.score_neocloud(m.get("neocloud_5d"))
    return None

def compute(state, m):
    rows = []
    for ind in C.INDICATORS:
        if ind["kind"] == "auto":
            e = auto_score(ind, m, state); esrc = "auto"
        else:
            e = state.get("manual", {}).get(ind["key"]); esrc = "manual"
        if e is None:
            hr = None; e_disp = None
        else:
            hr = ind["w"] * (5 - e) / 5 if ind["dir"] == "逆向" else ind["w"] * e / 5
            e_disp = e
        rows.append({**ind, "e": e_disp, "h": round(hr, 2) if hr is not None else 0.0,
                     "_hr": hr if hr is not None else 0.0, "esrc": esrc})
    valid_w = sum(r["w"] for r in rows if r["e"] is not None)
    total = round(sum(r["_hr"] for r in rows) / valid_w * 100, 1) if valid_w else 0.0
    missing = [r["key"] for r in rows if r["e"] is None]
    cat = {}
    for c in C.CATS:
        ws = sum(r["w"] for r in rows if r["cat"] == c and r["e"] is not None)
        hs = sum(r["_hr"] for r in rows if r["cat"] == c)
        cat[c] = round(hs / ws * 100, 1) if ws else 0
    # manual 新鲜度
    mu = state.get("manual_update"); days = None
    if mu:
        try: days = (datetime.now(ET).date() - datetime.fromisoformat(mu).date()).days
        except Exception: days = None
    manual_fresh = (days is not None and days <= C.FRESH_DAYS)
    # 置信度
    n_auto = sum(1 for r in rows if r["esrc"] == "auto" and r["e"] is not None)
    n_man  = sum(1 for r in rows if r["esrc"] == "manual" and r["e"] is not None)
    n_ind = len(C.INDICATORS)
    conf = round((n_auto + (n_man if manual_fresh else 0)) / n_ind, 2) if n_ind else 0

    sc = {r["key"]: r["e"] for r in rows}
    def s(k): return sc.get(k) if sc.get(k) is not None else 0.0
    # ── 四闸合取 ──
    rsi = m.get("rsi")
    g1 = (rsi is not None and rsi >= 70) or bool(state.get("sentiment_extreme"))           # ①情绪极端(必要)
    crack_keys = [k for k in ("A1","A2","A3","A4","A5","A6","B1","B2","B3","C1","C2","C3","D1","D2","D3","D4") if s(k) >= C.CRACK_SCORE]
    g2 = len(crack_keys) >= 1                                                                # ②≥1基本面裂缝
    g3 = s("E1") >= C.TECH_BREAK_SCORE                                                       # ③技术破位
    bf = state.get("borrow_fee_apr")
    exec_ok = state.get("exec_ok")
    g4 = (exec_ok is True) or (bf is not None and bf < 30)                                   # ④可执行
    short_ok = g1 and g2 and g3 and g4
    gates = {"①情绪极端": g1, "②基本面裂缝": g2, "③技术破位": g3, "④可执行": g4}
    gate_n = sum(1 for v in gates.values() if v)
    # ── falsifier(亮=别做空)──
    fz = {}
    fz["F1 现货未转跌"] = s("A1") < C.CRACK_SCORE
    fz["F2 合约仍涨/未松"] = s("A6") < C.CRACK_SCORE
    fz["F3 交期峰值/sold out"] = s("A3") < C.CRACK_SCORE
    fz["F4 capex 仍加速"] = s("B1") < C.CRACK_SCORE
    fz["F5 GM 指引上"] = s("D2") < C.CRACK_SCORE
    fz["F6 零降级/趋势完好"] = (s("E2") < C.CRACK_SCORE) and (s("E1") < C.TECH_BREAK_SCORE)
    fz["F7 YMTC 不降价"] = s("C1") < C.CRACK_SCORE
    fz["F8 三星/海力士守纪律"] = s("A4") < C.CRACK_SCORE
    fz["F9 借券飙升=逼空(别空)"] = (bf is not None and bf >= 50)
    fz["F10 bit/需求未恶化"] = (s("D1") < C.CRACK_SCORE) and (s("B3") < C.CRACK_SCORE)
    fz_lit = sum(1 for v in fz.values() if v)
    # IV 闸
    iv = m.get("iv30") if m.get("iv30") is not None else state.get("iv30")
    iv_label, iv_text, iv_factor, iv_color = C.iv_gate(iv)
    short_s, full, bcolor = C.band(total)
    if total >= 70 and not g3:
        short_s = f"{short_s}·待破位"
    # 今日动作
    if short_ok:
        action = f"四闸齐开 → 做空窗口。仅 put 借方价差,仓位×{iv_factor},设时间止损(借券负carry)"
    elif gate_n >= 2 and g3:
        action = f"破位已现但裂缝/执行未齐({gate_n}/4)→ 准备:挂 put 价差报价+备清单,不建仓"
    else:
        miss = [k for k, v in gates.items() if not v]
        action = f"不授权做空({gate_n}/4 闸)。缺:{('、'.join(miss))}。falsifier {fz_lit}/10 亮=上行有腿 → 坐着盯"
    return dict(rows=rows, total=total, cat=cat, missing=missing, conf=conf,
                manual_days=days, manual_fresh=manual_fresh,
                gates=gates, gate_n=gate_n, short_ok=short_ok, crack_keys=crack_keys,
                fz=fz, fz_lit=fz_lit, iv=iv, iv_label=iv_label, iv_text=iv_text,
                iv_factor=iv_factor, iv_color=iv_color,
                short=short_s, full=full, bcolor=bcolor, action=action)

# ---------- 历史 ----------
HIST = P("data_history.csv")
HCOLS = ["date","sndk","sndk_chg","rsi","sma50","iv30","total","gate_n","fz_lit","status"]
def append_history(m, r, state):
    date = m.get("date")
    if not date: return []
    existing = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    row = {"date": date, "sndk": m.get("sndk"), "sndk_chg": m.get("sndk_chg"),
           "rsi": m.get("rsi"), "sma50": m.get("sma50"), "iv30": r.get("iv"),
           "total": r["total"], "gate_n": r["gate_n"], "fz_lit": r["fz_lit"], "status": r["short"]}
    existing = [e for e in existing if e.get("date") != date]
    existing.append({k: ("" if row.get(k) is None else row.get(k)) for k in HCOLS})
    existing.sort(key=lambda e: e.get("date", ""))
    with open(HIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HCOLS); w.writeheader(); w.writerows(existing[-400:])
    return existing[-30:]

# ---------- 渲染 ----------
def esc(x): return html.escape(str(x)) if x is not None else ""
def fmt(x, n=0):
    try: return f"{float(x):,.{n}f}"
    except Exception: return "—"

def render(m, r, state, hist_tail):
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    ddate = m.get("date", "—")
    gate_html = "".join(
        f'<div class="trig {"on" if v else "off"}"><span>{esc(k)}</span><b>{"✅" if v else "⬜"}</b></div>'
        for k, v in r["gates"].items())
    fz_html = "".join(
        f'<div class="trig {"fzon" if v else "off"}"><span>{esc(k)}</span><b>{"🛑" if v else "⬜"}</b></div>'
        for k, v in r["fz"].items())
    cat_html = ""
    for c in C.CATS:
        v = r["cat"][c]
        col = "#C00000" if v >= 80 else "#ED7D31" if v >= 60 else "#FFC000" if v >= 40 else "#63BE7B"
        cat_html += (f'<div class="catrow"><span>{esc(c)}</span><div class="bar">'
                     f'<i style="width:{min(100,v):.0f}%;background:{col}"></i></div><b>{v:.0f}</b></div>')
    def badge(s): return '<em class="auto">自动</em>' if s == "auto" else '<em class="man">手填</em>'
    ind_html = ""; cur = None
    for row in r["rows"]:
        if row["cat"] != cur:
            cur = row["cat"]; ind_html += f'<tr class="cathead"><td colspan="4">{esc(cur)}</td></tr>'
        e = "—" if row["e"] is None else f'{row["e"]:.1f}'
        ind_html += (f'<tr><td>{esc(row["name"])} {badge(row["esrc"])}</td><td class="c">{row["w"]}</td>'
                     f'<td class="c">{e}</td><td class="c">{row["h"]:.1f}</td></tr>')
    cards = [
        ("SNDK", f'{fmt(m.get("sndk"),0)}' + (f' <small>({m["sndk_chg"]:+.2f}%)</small>' if "sndk_chg" in m else "")),
        ("RSI(14)", fmt(m.get("rsi"),1)), ("21-EMA", fmt(m.get("ema21"),0)), ("50-DMA", fmt(m.get("sma50"),0)),
        ("SNDK 5日", f'{m["sndk_5d"]:+.1f}%' if m.get("sndk_5d") is not None else "—"),
        ("MU/WDC 5日", f'{m["peer_5d"]:+.1f}%' if m.get("peer_5d") is not None else "—"),
        ("海力士/三星隔夜", f'{m["overnight"]:+.2f}%' if m.get("overnight") is not None else "—"),
        ("CRWV/ORCL 5日", f'{m["neocloud_5d"]:+.1f}%' if m.get("neocloud_5d") is not None else "—"),
        ("IV30", f'{r["iv"]:.0f}%' if r.get("iv") is not None else "—"),
    ]
    cards_html = "".join(f'<div class="card"><span>{esc(k)}</span><b>{v}</b></div>' for k, v in cards)
    warn = list(m.get("_warns") or [])
    if r.get("missing"): warn.append(f"缺数指标(已从分母剔除): {', '.join(r['missing'])}")
    md = r["manual_days"]
    if md is None: warn.append("手填项从未标更新日期(state.json 的 manual_update)")
    elif not r["manual_fresh"]: warn.append(f"手填项已 {md} 天未更新(>{C.FRESH_DAYS}),置信度下调")
    warn_html = ""
    if warn:
        lis = "".join(f"<li>{esc(w)}</li>" for w in warn)
        warn_html = (f'<div class="sec" style="border:1px solid #5a4d1a"><div class="sechd">⚠️ 数据质量</div>'
                     f'<ul style="font-size:12px;color:#e7c97a;padding-left:18px;line-height:1.7">{lis}</ul></div>')
    hist_html = ""
    for h in reversed(hist_tail[-12:]):
        hist_html += (f'<tr><td>{esc(h.get("date"))}</td><td class="c">{esc(h.get("sndk"))}</td>'
                      f'<td class="c">{esc(h.get("rsi"))}</td><td class="c">{esc(h.get("total"))}</td>'
                      f'<td class="c">{esc(h.get("gate_n"))}</td><td class="c">{esc(h.get("status"))}</td></tr>')
    return f"""<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SanDisk 做空择时评分卡</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6;
 max-width:520px;margin:0 auto;padding:12px 12px 40px;font-size:15px;line-height:1.5}}
h1{{font-size:18px;font-weight:700}} .sub{{color:#8b93a7;font-size:11px;margin-bottom:10px}}
.sec{{background:#1a1d27;border-radius:12px;padding:12px;margin:10px 0;box-shadow:0 1px 3px #0006}}
.sechd{{font-size:12px;color:#9aa3b8;font-weight:700;margin-bottom:8px;letter-spacing:.5px}}
.total{{display:flex;align-items:center;gap:14px}}
.bignum{{font-size:54px;font-weight:800;line-height:1}}
.statusbox{{padding:10px 14px;border-radius:10px;color:#fff;font-weight:700;font-size:16px;text-align:center;min-width:96px}}
.full{{margin-top:8px;font-size:14px;color:#cfd6e6}}
.action{{background:#2a2410;border:1px solid #5a4d1a;border-radius:10px;padding:11px;font-size:14px;font-weight:600;color:#ffe9a8}}
.trig{{display:flex;justify-content:space-between;align-items:center;padding:7px 4px;border-bottom:1px solid #262a36;font-size:13px}}
.trig:last-child{{border:0}} .trig b{{font-size:15px}} .trig.on{{color:#ff9a9a}} .trig.fzon{{color:#7fe0a0}} .trig.off{{color:#7f8aa3}}
.trigtot{{text-align:right;font-size:20px;font-weight:800;margin-top:4px}}
.ivbox{{padding:10px;border-radius:10px;color:#fff}} .ivbox b{{font-size:15px}} .ivbox p{{font-size:12px;margin-top:4px;opacity:.95}}
.catrow{{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px}}
.catrow span{{width:104px;flex:none}} .catrow b{{width:34px;text-align:right}}
.bar{{flex:1;background:#262a36;border-radius:6px;height:12px;overflow:hidden}} .bar i{{display:block;height:100%}}
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.card{{background:#11141c;border-radius:9px;padding:8px 10px;display:flex;flex-direction:column}}
.card span{{font-size:11px;color:#8b93a7}} .card b{{font-size:17px;margin-top:2px}} .card small{{font-size:11px;color:#8b93a7}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:5px 6px;border-bottom:1px solid #232734;text-align:left}} td.c{{text-align:center}}
.cathead td{{background:#11141c;color:#9aa3b8;font-weight:700}}
em{{font-style:normal;font-size:10px;padding:1px 5px;border-radius:4px;margin-left:4px}}
em.auto{{background:#16361f;color:#7fe0a0}} em.man{{background:#3a2f12;color:#e7c97a}}
.foot{{font-size:11px;color:#6b7390;margin-top:14px;line-height:1.6}}
</style></head><body>
<h1>📉 SanDisk (SNDK) 做空择时评分卡</h1>
<div class="sub">NAND 周期顶 + 最弱环节 · 只在四闸齐开做空 · 抓第二腿不抓顶　|　数据日 {esc(ddate)}　刷新 {esc(now)}</div>
{warn_html}
<div class="sec"><div class="total">
 <div class="bignum" style="color:{r['bcolor']}">{r['total']:.1f}</div>
 <div class="statusbox" style="background:{r['bcolor']}">{esc(r['short'])}</div>
</div><div class="full">{esc(r['full'])}</div></div>
<div class="sec"><div class="sechd">📌 今日动作</div><div class="action">{esc(r['action'])}</div></div>
<div class="sec"><div class="sechd">🚦 四闸合取(全开才授权做空)</div>{gate_html}
<div class="trigtot">{r['gate_n']} / 4</div></div>
<div class="sec"><div class="sechd">⚠️ IV 闸(IV 越高 put 越贵)</div>
<div class="ivbox" style="background:{r['iv_color']}"><b>{esc(r['iv_label'])}　IV {fmt(r.get('iv'),0)}%　仓位×{r['iv_factor']}</b>
<p>{esc(r['iv_text'])}</p></div></div>
<div class="sec"><div class="sechd">🛑 Falsifier(亮=上行有腿·别做空)</div>{fz_html}
<div class="trigtot">{r['fz_lit']} / 10 亮</div></div>
<div class="sec"><div class="sechd">五大类别</div>{cat_html}
<div style="font-size:12px;color:#8b93a7;margin-top:6px">数据置信度 {int(r['conf']*100)}%</div></div>
<div class="sec"><div class="sechd">📡 自动抓取(yfinance)</div><div class="cards">{cards_html}</div></div>
<div class="sec"><div class="sechd">指标明细(自动=实时/手填=判断或无免费源)</div>
<table><tr><th>指标</th><th class="c">权重</th><th class="c">分</th><th class="c">贡献</th></tr>{ind_html}</table></div>
<div class="sec"><div class="sechd">近期历史</div>
<table><tr><th>日期</th><th class="c">SNDK</th><th class="c">RSI</th><th class="c">总分</th><th class="c">闸</th><th class="c">状态</th></tr>{hist_html}</table></div>
<div class="foot">
核心纪律:只在 ①情绪极端 + ②≥1基本面裂缝 + ③技术破位 + ④可执行 四闸齐开才做空;只用 put 借方价差(IV高,裸put −EV);抓第二腿不抓顶。<br>
手填项在仓库 <b>state.json</b> 更新(NAND 现货/合约、capex 语言、YMTC 定价、bit/GM、借券费、降级 等),改后推送自动重算。<br>
⚠️ 个人择时辅助,非投资建议、非预测。期权可能归零。做空前 first-hand:SNDK 实时报价、借券成本/可借量、3Q26 NAND 合约指引(7月)、SNDK 财报日(~8月,IR 确认)。
</div></body></html>"""

# ---------- 主流程 ----------
def validate_state(state):
    warns = []
    man = state.get("manual") or {}
    for k, v in list(man.items()):
        try:
            fv = float(v)
            if not math.isfinite(fv): raise ValueError
        except Exception:
            warns.append(f"manual.{k}=「{v}」非数 → 缺数处理"); man[k] = None; continue
        cv = min(5.0, max(0.0, fv))
        if cv != fv: warns.append(f"manual.{k}={fv} 越界 → 钳制 {cv}")
        man[k] = cv
    return warns

def main():
    with open(P("state.json"), encoding="utf-8") as f:
        state = json.load(f)
    warns = validate_state(state)
    m = fetch_market()
    if "sndk" not in m:
        print("[fatal] SNDK 行情抓取失败,保留上次页面", file=sys.stderr); sys.exit(1)
    m["_warns"] = warns
    r = compute(state, m)
    hist_tail = append_history(m, r, state) or []
    with open(P("index.html"), "w", encoding="utf-8") as f:
        f.write(render(m, r, state, hist_tail))
    status = dict(date=m.get("date"), refreshed=datetime.now(ET).isoformat(timespec="minutes"),
                  total=r["total"], status=r["short"], gates=r["gates"], gate_n=r["gate_n"],
                  short_ok=r["short_ok"], fz_lit=r["fz_lit"], conf=r["conf"], categories=r["cat"],
                  sndk=m.get("sndk"), rsi=m.get("rsi"), iv30=r.get("iv"), missing=r["missing"], warns=warns)
    with open(P("status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"OK 数据日={m.get('date')} 总分={r['total']} 状态={r['short']} 四闸={r['gate_n']}/4 "
          f"falsifier={r['fz_lit']}/10 置信={r['conf']} SNDK={m.get('sndk')} RSI={m.get('rsi')}")

if __name__ == "__main__":
    main()
