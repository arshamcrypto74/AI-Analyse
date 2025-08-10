# main.py — تلگرام خودکار (بدون chat_id دستی) + بدون parse_mode + fallback صرافی‌ها
import os, requests, pandas as pd, numpy as np, ccxt
from datetime import datetime, timezone

# ===== تنظیمات از Secrets/Variables =====
TOKEN     = os.environ.get("TELEGRAM_TOKEN")           # از Secrets
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")         # اگر خالی/اشتباه باشد، خودکار پیدا می‌کنیم
SYMBOL    = os.environ.get("SYMBOL", "BTC/USDT")
TIMEFRAME = os.environ.get("TIMEFRAME", "15m")
EQUITY    = float(os.environ.get("EQUITY", "3000"))
RISK      = float(os.environ.get("RISK", "0.01"))
MAX_LEV   = float(os.environ.get("MAX_LEV", "5"))
EXCHANGES = [s.strip() for s in os.environ.get("EXCHANGES", "bybit,bitget,okx").split(",")]

# ===== پارامترهای استراتژی نمونه =====
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

# ====== Telegram helpers ======
def _bot_id_from_token(token: str) -> str:
    try:
        return str(token.split(":")[0])
    except Exception:
        return ""

def find_chat_id(token: str, max_tries: int = 5) -> str:
    """آخرین chat_id از getUpdates که فرستنده‌اش bot نیست (DM کاربر) برمی‌گرداند.
       اگر کانال باشد و bot ادمین باشد، channel_post را هم قبول می‌کند."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    for _ in range(max_tries):
        try:
            j = requests.get(url, timeout=10).json()
            for upd in reversed(j.get("result", [])):
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat = msg.get("chat") or {}
                frm  = msg.get("from") or {}
                # فقط اگر فرستنده bot نباشد (DM کاربر). برای کانال ممکن است 'from' نباشد.
                if frm and frm.get("is_bot"):
                    continue
                cid = chat.get("id")
                if cid is not None:
                    print(f"[telegram] autodetected chat_id {cid} type={chat.get('type')}")
                    return str(cid)
        except Exception as e:
            print("find_chat_id error:", e)
    return ""

def notify(text: str) -> bool:
    """ارسال پیام بدون parse_mode تا خطای Markdown نده."""
    if not TOKEN:
        print("[telegram] TOKEN not set"); return False
    global CHAT_ID
    # اگر کاربر قبلاً chat_id را Secret گذاشته ولی اشتباهاً bot id است، نادیده بگیر
    if CHAT_ID:
        if CHAT_ID == _bot_id_from_token(TOKEN):
            print("[telegram] provided CHAT_ID is bot id; ignoring and autodetecting...")
            CHAT_ID = None
        else:
            CHAT_ID = str(CHAT_ID)

    if not CHAT_ID:
        CHAT_ID = find_chat_id(TOKEN)
        if not CHAT_ID:
            print("[telegram] CHAT_ID not found yet; send /start to your bot and re-run.")
            return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
        j = r.json()
        if not j.get("ok", False):
            print(f"[telegram] send failed: {j.get('description')} ({j.get('error_code')})")
            return False
        return True
    except Exception as e:
        print("telegram exception:", e)
        return False

# ====== Data with exchange fallback ======
def pick_symbol_for_exchange(ex_id: str, symbol: str) -> str:
    # بیشتر صرافی‌های پرپچوال USDT نماد را به صورت BTC/USDT:USDT می‌خواهند
    if ex_id in ["bybit","bitget","okx","bingx","kucoinfutures","phemex"]:
        return symbol.replace("/USDT","/USDT:USDT")
    return symbol

def fetch_ohlcv_any(symbol, timeframe, limit):
    last_err = None
    for ex_id in EXCHANGES:
        try:
            if not hasattr(ccxt, ex_id):
                continue
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
            sym = pick_symbol_for_exchange(ex_id, symbol)
            data = ex.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
            print(f"[data] using {ex_id} symbol {sym}")
            return data, ex_id
        except Exception as e:
            last_err = f"{ex_id}: {type(e).__name__}: {e}"
            print("[data] fail ->", last_err)
            continue
    raise Exception("All exchanges failed. Last error: " + str(last_err))

# ====== Strategy ======
def run_once():
    data, ex_id = fetch_ohlcv_any(SYMBOL, TIMEFRAME, LOOKBACK)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["emaS"] = ema(df["close"], EMA_SLOW)
    df["rsi"]  = rsi(df["close"], RSI_LEN)
    df["atr"]  = atr(df, ATR_LEN)
    if len(df) < max(EMA_SLOW, RSI_LEN, ATR_LEN) + 2:
        print("Not enough data"); return

    c0,c1 = df["close"].iloc[-2], df["close"].iloc[-1]
    e0,e1 = df["emaS"].iloc[-2],  df["emaS"].iloc[-1]
    r1    = float(df["rsi"].iloc[-1])
    a1    = float(df["atr"].iloc[-1])

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
    txt=(f"ALERT {SYMBOL} {TIMEFRAME} via {ex_id}\n"
         f"Side: {side.upper()}\n"
         f"Entry: {entry:.2f}  Stop: {stop:.2f}  TP: {tp:.2f}\n"
         f"Qty: {qty:.6f}  Notional: ${notional:.2f}  Risk: ${risk_usd:.2f}\n"
         f"RSI: {r1:.1f}  Time: {now}")
    notify(txt)

# ====== Main ======
if __name__ == "__main__":
    # دیگه پیام تست نمی‌فرستیم؛ فقط وقتی سیگنال باشد پیام می‌آید.
    run_once()
