import os, requests, pandas as pd, numpy as np, ccxt
from datetime import datetime, timezone

# از Settings → Secrets/Variables خوانده می‌شوند (توکن را داخل کد نگذار)
TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SYMBOL  = os.environ.get("SYMBOL", "BTC/USDT")
TIMEFRAME = os.environ.get("TIMEFRAME", "15m")
EQUITY  = float(os.environ.get("EQUITY", "3000"))
RISK    = float(os.environ.get("RISK", "0.01"))
MAX_LEV = float(os.environ.get("MAX_LEV", "5"))

EMA_SLOW=50; RSI_LEN=14; RSI_LONG=55; RSI_SHORT=45
ATR_LEN=14; ATR_MUL=1.5; RR=2.0; LOOKBACK=400

ema=lambda s,n: s.ewm(span=n, adjust=False).mean()
def rsi(s,n=14):
    d=s.diff(); up=(d.where(d>0,0)).rolling(n).mean(); dn=(-d.where(d<0,0)).rolling(n).mean()
    rs=up/(dn+1e-12); return 100-(100/(1+rs))
def atr(df,n=14):
    pc=df['close'].shift(1)
    tr=pd.concat([(df['high']-df['low']).abs(), (df['high']-pc).abs(), (df['low']-pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def notify(text):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown"}, timeout=10)

def run_once():
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    data = ex.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LOOKBACK)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"]=pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["emaS"]=ema(df["close"], EMA_SLOW)
    df["rsi"]=rsi(df["close"], RSI_LEN)
    df["atr"]=atr(df, ATR_LEN)
    if len(df)<max(EMA_SLOW,RSI_LEN,ATR_LEN)+2: return
    c0,c1=df["close"].iloc[-2],df["close"].iloc[-1]
    e0,e1=df["emaS"].iloc[-2],df["emaS"].iloc[-1]
    r1=float(df["rsi"].iloc[-1]); a1=float(df["atr"].iloc[-1])
    side=None
    if c0<=e0 and c1>e1 and r1>RSI_LONG:
        side="long"; entry=float(c1); stop=float(entry-a1*ATR_MUL); risk=entry-stop; tp=float(entry+RR*risk)
    elif c0>=e0 and c1<e1 and r1<RSI_SHORT:
        side="short"; entry=float(c1); stop=float(entry+a1*ATR_MUL); risk=stop-entry; tp=float(entry-RR*risk)
    if not side: return
    risk_usd=max(EQUITY*RISK,0.0); qty=risk_usd/max(abs(entry-stop),1e-8); notional=qty*entry
    cap=EQUITY*MAX_LEV
    if notional>cap:
        sc=cap/notional; qty*=sc; notional=cap
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    txt=(f"*ALERT* {SYMBOL} {TIMEFRAME}\n"
         f"Side: *{side.upper()}*\n"
         f"Entry: `{entry:.2f}`  Stop: `{stop:.2f}`  TP: `{tp:.2f}`\n"
         f"Qty: `{qty:.6f}`  Notional: `${notional:.2f}`  Risk: `${risk_usd:.2f}`\n"
         f"RSI: `{r1:.1f}`  Time: `{now}`")
    notify(txt)

if __name__ == "__main__":
    run_once()
