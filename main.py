import os, requests, pandas as pd, numpy as np, ccxt
from datetime import datetime, timezone

# === تنظیمات از Secrets/Variables ===
TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOL  = os.environ.get("SYMBOL", "BTC/USDT")   # ورودی ساده
TIMEFRAME = os.environ.get("TIMEFRAME", "15m")
EQUITY  = float(os.environ.get("EQUITY", "3000"))
RISK    = float(os.environ.get("RISK", "0.01"))
MAX_LEV = float(os.environ.get("MAX_LEV", "5"))

# لیست صرافی‌ها به ترتیب اولویت (با کاما جدا شود؛ در Variables هم می‌توان عوض کرد)
EXCHANGES = [s.strip() for s in os.environ.get("EXCHANGES", "bybit,bitget,okx").split(",")]

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
    if not TOKEN or not CHAT_ID:
        print("[telegram] TOKEN/CHAT_ID not set"); return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown"}, timeout=10)
    except Exception as e:
        print("telegram error:", e)

def pick_symbol_for_exchange(ex_id: str, symbol: str) -> str:
    # بیشتر صرافی‌های فیوچرز USDT-Perp نماد را به صورت BTC/USDT:USDT می‌خواهند
    if ex_id in ["bybit", "bitget", "okx", "bingx", "kucoinfutures", "phemex"]:
        return symbol.replace("/USDT", "/USDT:USDT")
    return symbol

def fetch_ohlcv_any(symbol, timeframe, limit):
    last_err = None
    for ex_id in EXCHANGES:
        try:
            if not hasattr(ccxt, ex_id):  # اگر کتابخانه آن صرافی را ندارد
                continue
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
            sym = pick_symbol_for_exchange(ex_id, symbol)
            data = ex.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
            return data, ex_id, sym
        except Exception as e:
            last_err = f"{ex_id}: {type(e).__name__}: {e}"
            continue
    raise Exception("All exchanges failed. Last error: " + str(last_err))

def run_once():
    try:
        data, ex_id, used_symbol = fetch_ohlcv_any(SYMBOL, TIMEFRAME, LOOKBACK)
    except Exception as e:
        notify(f"⚠️ Fetch error on all exchanges: {e}")
        raise

    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"]=pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["emaS"]=ema(df["close"], EMA_SLOW)
    df["rsi"]=rsi(df["close"], RSI_LEN)
    df["atr"]=atr(df, ATR_LEN)
    if len(df)<max(EMA_SLOW,RSI_LEN,ATR_LEN)+2:
        print("Not enough data"); return

    c0,c1=df["close"].iloc[-2],df["close"].iloc[-1]
    e0,e1=df["emaS"].iloc[-2],df["emaS"].iloc[-1]
    r1=float(df["rsi"].iloc[-1]); a1=float(df["atr"].iloc[-1])
    side=None
    if c0<=e0 and c1>e1 and r1>RSI_LONG:
        side="long"; entry=float(c1); stop=float(entry-a1*ATR_MUL); risk=entry-stop; tp=float(entry+RR*risk)
    elif c0>=e0 and c1<e1 and r1<RSI_SHORT:
        side="short"; entry=float(c1); stop=float(entry+a1*ATR_MUL); risk=stop-entry; tp=float(entry-RR*risk)
    if not side:
        print("No signal"); return

    risk_usd=max(EQUITY*RISK,0.0); qty=risk_usd/max(abs(entry-stop),1e-8); notional=qty*entry
    cap=EQUITY*MAX_LEV
    if notional>cap:
        sc=cap/notional; qty*=sc; notional=cap
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    txt=(f"*ALERT* {SYMBOL} {TIMEFRAME} via {ex_id}\n"
         f"Side: *{side.upper()}*\n"
         f"Entry: `{entry:.2f}`  Stop: `{stop:.2f}`  TP: `{tp:.2f}`\n"
         f"Qty: `{qty:.6f}`  Notional: `${notional:.2f}`  Risk: `${risk_usd:.2f}`\n"
         f"RSI: `{r1:.1f}`  Time: `{now}`")
    notify(txt)

if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        raise SystemExit("❌ TELEGRAM_TOKEN/TELEGRAM_CHAT_ID تعریف نشده.")
    notify("🟢 GitHub Actions روشن شد (fallback exchanges فعال).")
    run_once()
