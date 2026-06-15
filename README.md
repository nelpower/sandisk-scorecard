# SanDisk (SNDK) 做空择时评分卡 · 网页版

镜像 `korea-scorecard/web` 引擎。auto 层用 yfinance(SNDK 技术=强制确认闸、MU/WDC 相对强度、海力士/三星隔夜 tape、CoreWeave/Oracle neocloud、IV30);基本面层 `state.json` 手填。

**核心纪律**:只在 ①情绪极端 + ②≥1 基本面裂缝 + ③技术破位 + ④可执行 **四闸齐开**才授权做空;只用 put 借方价差(IV 高,裸 put −EV);抓第二腿不抓顶。

## 本地运行
```
cd web
pip install -r requirements.txt
python refresh.py        # 生成 index.html + status.json + data_history.csv
```

## 上 GitHub Pages
1. 新建 repo,把本目录推上去(`web/` 作为根或子目录均可,调整 Pages 源)。
2. Settings → Pages → Source = `main` 分支 `/web`(或 `/(root)`)。
3. `index.html` 即看板;`.github/workflows/refresh.yml` 每交易日 3 班自动重算并提交。
4. 手填项改 `state.json` 后 push → workflow 自动重算。

## 手填 state.json
- `manual.{A1..E5}`:0-5,越高越利做空(读数见 `_readings`)
- `borrow_fee_apr`:借券年化费(填实际值;<30 才开④可执行闸;null=保守不可执行)
- `iv30`:IV 兜底(auto 抓不到时用)
- `sentiment_extreme`:情绪极端布尔(①)
- `manual_update`:更新日期(>5 天置信度下调)

## 可后续接成 auto 的手填项
Form-4(EDGAR RSS,SNDK CIK 0002023554)、出口管制(Federal Register API)、借券费(Fintel,需 ScraperAPI 兜底)。NAND 现货/合约(TrendForce 无免费 API)永远手填——这是"唯一值得花钱"的升级。

⚠️ 个人择时辅助,非投资建议、非预测。做空前 first-hand:SNDK 实时报价、借券成本/可借量、3Q26 NAND 合约指引、SNDK 财报日。
