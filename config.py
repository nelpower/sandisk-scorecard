# -*- coding: utf-8 -*-
"""
SanDisk (SNDK) 做空择时评分卡 — 评分配置(镜像 korea-scorecard 引擎)
- 每指标: key, cat, w, name, dir(正向=做空利好), kind(auto/manual), auto_fn, src
- auto: 由当日 yfinance 抓的数据按下方规则算分(0~5,越高=越利空/越利做空)
- manual: 从 state.json['manual'][key] 读(无免费实时源 / 判断类)
- 改框架/权重/阈值只改这个文件
- 核心纪律: 只在 ①情绪极端 + ②≥1基本面裂缝 + ③技术破位 + ④可执行 四闸齐开才授权做空
"""

def _nn(x):
    return x is None or (isinstance(x, float) and x != x)

# ---------- 自动打分(数据 -> 0~5,越高=越利做空) ----------
def score_tech(px, ema21, sma50, rsi):
    """E1 技术破位序列【强制确认闸】。高分=破位确认(利做空);趋势完好(在均线上)=低分,
    即使 RSI 超买也不算破位(Micron 陷阱:超买可持续~1年)。"""
    if _nn(px) or _nn(sma50):
        return None
    if not _nn(ema21):
        if px < sma50 and px < ema21:  s = 5.0   # 双失=破位确认
        elif px < sma50:                s = 4.0
        elif px < ema21:                s = 3.0   # 仅失快线
        else:                           s = 1.0   # 在双均线上=趋势完好,不是做空
    else:
        s = 4.0 if px < sma50 else 1.0
    if not _nn(rsi) and px >= sma50 and rsi >= 70:
        s = min(s, 1.5)   # 超买+趋势完好 → 强制压低,别把超买当破位
    return s

def score_rs(sndk_5d, peer_5d):
    """D4/E3 相对强度: SNDK 跑输同业=早期派发(利做空);领先=动量完好。"""
    if _nn(sndk_5d) or _nn(peer_5d):
        return None
    d = sndk_5d - peer_5d
    if d <= -8: return 4.5
    if d <= -4: return 4.0
    if d <= 0:  return 3.0
    if d <= 5:  return 2.0
    return 1.0

def score_overnight(avg_chg):
    """E3 韩股隔夜 tape(海力士+三星亚洲时段): 内存复合体隔夜下行=美开盘前的领先利空。"""
    if _nn(avg_chg): return None
    if avg_chg <= -4: return 4.5
    if avg_chg <= -2: return 4.0
    if avg_chg <= 0:  return 3.0
    if avg_chg <= 3:  return 2.0
    return 1.0

def score_neocloud(avg_5d):
    """B2 AI-neocloud 信用代理(CoreWeave/Oracle 5日): 破位=变现缺口压力(利做空)。"""
    if _nn(avg_5d): return None
    if avg_5d <= -10: return 4.5
    if avg_5d <= -5:  return 4.0
    if avg_5d <= 0:   return 3.0
    if avg_5d <= 5:   return 2.0
    return 1.5

def score_borrow(fee_apr):
    """E5 借券/HTB: 既是风险/执行闸也是信号。低费=空头便宜加(enabling);
    飙升=逼空燃料/不可执行(利多/别空)。"""
    if _nn(fee_apr): return None
    if fee_apr >= 50: return 1.0   # 不可执行/逼空燃料
    if fee_apr >= 30: return 2.0
    if fee_apr >= 15: return 3.0
    return 3.5

def iv_gate(iv30):
    """IV 越高 put 越贵。未知=最保守档(×0.4)。"""
    if _nn(iv30): return ("⚪ IV未知", "IV 缺失→最保守:只 put 借方价差×0.4", 0.4, "#9E9E9E")
    if iv30 >= 90: return ("🔴 过贵", "禁裸 put(−EV)。只 put 借方价差×0.4", 0.4, "#7030A0")
    if iv30 >= 60: return ("🔴 偏贵", "优先 put 价差,仓位×0.6", 0.6, "#C00000")
    if iv30 >= 40: return ("🟡 偏贵", "近月价差/小仓×0.8", 0.8, "#FFC000")
    return ("🟢 可裸买", "IV 正常,裸 put 可接受", 1.0, "#63BE7B")

# ---------- 指标定义(0-5 制) ----------
INDICATORS = [
 # A 价格/供给领先 (30)
 dict(key="A1", cat="A 价格/供给", w=6, name="NAND 512Gb TLC 现货周方向(心跳)", dir="正向", kind="manual", note="TrendForce 公开现货页·无免费API·周读", src="TF"),
 dict(key="A2", cat="A 价格/供给", w=5, name="现货−合约价差收敛(由现货下行)", dir="正向", kind="manual", note="付费源,免费粗略", src="TF"),
 dict(key="A3", cat="A 价格/供给", w=4, name="交期 PEAK 后回落", dir="正向", kind="manual", note="Findchips/Digitimes 定性", src="DG"),
 dict(key="A4", cat="A 价格/供给", w=5, name="三星纪律破坏/产能回流 NAND", dir="正向", kind="manual", note="三星是定价者;最可能破纪律", src="TF"),
 dict(key="A5", cat="A 价格/供给", w=5, name="量价背离+取消/双订单解除", dir="正向", kind="manual", note="TrendForce 周评·已 AMBER", src="TF"),
 dict(key="A6", cat="A 价格/供给", w=5, name="DRAM/HBM 合约动能(第一多米诺)", dir="正向", kind="manual", note="DDR5/HBM 领先 NAND 数周", src="TF"),
 # B 需求源领先 (25)
 dict(key="B1", cat="B 需求源", w=9, name="超大厂 capex 指引二阶导", dir="正向", kind="manual", note="读5-6家财报电话·季频", src="ER"),
 dict(key="B2", cat="B 需求源", w=9, name="AI变现缺口 + neocloud信用(CRWV/ORCL)", dir="正向", kind="auto", auto="neocloud", note="CoreWeave/Oracle 5日动能(自动)+债利差(手填)", src="MX"),
 dict(key="B3", cat="B 需求源", w=7, name="消费需求摧毁+云库存双订单", dir="正向", kind="manual", note="IDC/Counterpoint·与企业端同弱才转空", src="IDC"),
 # C 中国/长江 (10)
 dict(key="C1", cat="C 中国/长江", w=4, name="YMTC/CXMT 定价姿态(洪水leg-1)", dir="正向", kind="manual", note="降价=洪水;现仅拼供给不拼价", src="DG"),
 dict(key="C2", cat="C 中国/长江", w=3, name="出口管制 flood-gate", dir="正向", kind="manual", note="Federal Register·放松=利空;现收紧", src="FR"),
 dict(key="C3", cat="C 中国/长江", w=3, name="长江份额抢占+企业eSSD认证", dir="正向", kind="manual", note="份额8→13%;企业认证=kill-shot", src="DG"),
 # D 公司特定 (15)
 dict(key="D1", cat="D 公司特定", w=5, name="bit 出货环比趋势(最佳公司领先)", dir="正向", kind="manual", note="10-Q/电话·季频·Q3已环比降", src="ER"),
 dict(key="D2", cat="D 公司特定", w=4, name="毛利指引下调", dir="正向", kind="manual", note="任何从79-81%的下修", src="ER"),
 dict(key="D3", cat="D 公司特定", w=3, name="合约覆盖缺口(~60%现货敞口)", dir="正向", kind="manual", note="放大器非扳机", src="ER"),
 dict(key="D4", cat="D 公司特定", w=3, name="NAND同业RS(MU/WDC,WDC现HDD)", dir="正向", kind="auto", auto="rs_peer", note="SNDK vs MU/WDC 5日;真NAND比=Kioxia/MU", src="YF"),
 # E 情绪/持仓/技术 (20) — 强制确认闸
 dict(key="E1", cat="E 情绪/技术", w=6, name="技术破位序列【强制确认闸】", dir="正向", kind="auto", auto="tech", note="RSI/21-EMA/50-DMA·自动", src="YF"),
 dict(key="E2", cat="E 情绪/技术", w=4, name="首次降级/EPS下修/好财报却跌", dir="正向", kind="manual", note="TipRanks/MarketBeat·现全升", src="TR"),
 dict(key="E3", cat="E 情绪/技术", w=3, name="相对强度+韩股隔夜tape", dir="正向", kind="auto", auto="overnight", note="海力士/三星亚洲收盘(自动)", src="YF"),
 dict(key="E4", cat="E 情绪/技术", w=3, name="内部人 Form-4 抛售速度", dir="正向", kind="manual", note="EDGAR·3+非10b5-1集群=信号", src="EDGAR"),
 dict(key="E5", cat="E 情绪/技术", w=4, name="借券成本/HTB(执行闸+逼空表)", dir="正向", kind="manual", note="Fintel·飙升=逼空燃料/不可执行", src="FT"),
]

CATS = ["A 价格/供给", "B 需求源", "C 中国/长江", "D 公司特定", "E 情绪/技术"]

# ---------- 动作带 ----------
def band(total):
    if _nn(total): return ("数据异常", "总分非数 — 检查数据源/state.json", "#9E9E9E")
    if total < 35: return ("只记录", "0–35 / 只记录", "#63BE7B")
    if total < 55: return ("监控", "35–55 / 重点监控", "#A9D08E")
    if total < 70: return ("观察", "55–70 / 观察", "#FFC000")
    if total < 85: return ("准备", "70–85 / 准备(待裂缝+破位)", "#ED7D31")
    return ("做空窗口", "85–100 / 三闸齐开做空窗口", "#C00000")

# manual 新鲜度阈值(自然日)
FRESH_DAYS = 5
# 基本面裂缝判定阈值(A/B/C/D 任一指标 >= 此分 = 一道裂缝在 rolling)
CRACK_SCORE = 3.5
# 技术破位确认阈值(E1 >= 此分 = ③确认)
TECH_BREAK_SCORE = 4.0
