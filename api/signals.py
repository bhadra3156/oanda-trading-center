"""H4 Signal Engine — RSI + EMA + MACD + ATR + Donchian + Session + MTF"""
import logging
from datetime import datetime
logger = logging.getLogger(__name__)

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag = sum(gains[-period:])/period
    al = sum(losses[-period:])/period
    for i in range(period, len(gains)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    return round(100-(100/(1+ag/al)),2) if al!=0 else 100.0

def calc_ema(closes, period):
    if len(closes)<period: return None
    k=2/(period+1); ema=sum(closes[:period])/period
    for c in closes[period:]: ema=c*k+ema*(1-k)
    return round(ema,6)

def calc_atr(candles, period=14):
    if len(candles)<period+1: return None
    trs=[max(c["high"]-c["low"],abs(c["high"]-candles[i-1]["close"]),
             abs(c["low"]-candles[i-1]["close"]))
         for i,c in enumerate(candles[1:],1)]
    atr=sum(trs[:period])/period
    for tr in trs[period:]: atr=(atr*(period-1)+tr)/period
    return round(atr,6)

def calc_macd(closes, fast=12, slow=26):
    if len(closes)<slow+1: return None,None,None
    ef=calc_ema(closes,fast); es=calc_ema(closes,slow)
    ef2=calc_ema(closes[:-1],fast); es2=calc_ema(closes[:-1],slow)
    if not all([ef,es,ef2,es2]): return None,None,None
    macd=ef-es; prev=ef2-es2
    return round(macd,6), macd>0 and prev<=0, macd<0 and prev>=0

def get_pip(instrument):
    if "JPY"  in instrument: return 0.01
    if "XAU"  in instrument: return 0.1
    if any(x in instrument for x in ["BCO","WTICO","XPD","XAG","NATGAS","CORN","SUGAR","WHEAT","SOYBN"]): return 0.01
    if any(x in instrument for x in ["SPX","NAS","UK1","DE3"]): return 1.0
    return 0.0001

def get_session():
    h=datetime.utcnow().hour
    if 7<=h<=16:  return "LONDON",1.0
    if 13<=h<=21: return "NEW_YORK",1.0
    return "ASIAN",0.6

class SignalEngine:
    def __init__(self, oanda_client):
        self.client = oanda_client

    def analyse(self, instrument):
        try:
            candles=self.client.get_candles(instrument,granularity="H4",count=250)
            if len(candles)<210:
                return {"instrument":instrument,"signal":"WAIT",
                        "reasons":["Insufficient H4 data"],"confidence":0}
            closes=[c["close"] for c in candles]
            highs=[c["high"] for c in candles]
            lows=[c["low"] for c in candles]
            vols=[c["volume"] for c in candles]
            rsi=calc_rsi(closes,14)
            ema20=calc_ema(closes,20); ema50=calc_ema(closes,50)
            ema200=calc_ema(closes,200); atr=calc_atr(candles,14)
            macd,macd_bull,macd_bear=calc_macd(closes)
            don_high=max(highs[-21:-1]) if len(highs)>21 else None
            don_low=min(lows[-21:-1]) if len(lows)>21 else None
            latest=candles[-1]; prev=candles[-2]; price=latest["close"]
            avg_vol=sum(vols[-20:])/20 if len(vols)>=20 else 1
            vol_surge=vols[-1]>avg_vol*1.15
            session,smult=get_session()
            pip=get_pip(instrument)
            trend=("BULLISH" if ema200 and price>ema200 else
                   "BEARISH" if ema200 and price<ema200 else "NEUTRAL")
            bs,ss=0,0; br,sr=[],[]
            if rsi and rsi<35:   bs+=30; br.append(f"RSI {rsi:.1f} oversold on H4")
            elif rsi and rsi<42: bs+=15; br.append(f"RSI {rsi:.1f} approaching oversold")
            if rsi and rsi>65:   ss+=30; sr.append(f"RSI {rsi:.1f} overbought on H4")
            elif rsi and rsi>58: ss+=15; sr.append(f"RSI {rsi:.1f} approaching overbought")
            if ema200 and price>ema200: bs+=20; br.append("Price above 200 EMA — uptrend")
            if ema200 and price<ema200: ss+=20; sr.append("Price below 200 EMA — downtrend")
            if ema50 and ema200 and ema50>ema200: bs+=12; br.append("Golden cross: 50>200 EMA")
            if ema50 and ema200 and ema50<ema200: ss+=12; sr.append("Death cross: 50<200 EMA")
            if ema20 and ema50 and ema20>ema50: bs+=8; br.append("20 EMA > 50 EMA")
            if ema20 and ema50 and ema20<ema50: ss+=8; sr.append("20 EMA < 50 EMA")
            if macd_bull: bs+=20; br.append("MACD crossed above zero")
            if macd_bear: ss+=20; sr.append("MACD crossed below zero")
            if don_high and prev["close"]<don_high<=latest["close"]:
                bs+=18; br.append(f"H4 breakout above {don_high:.4f}")
            if don_low and prev["close"]>don_low>=latest["close"]:
                ss+=18; sr.append(f"H4 breakdown below {don_low:.4f}")
            if vol_surge:
                if bs>=ss: bs+=5; br.append("Volume surge confirms")
                else: ss+=5; sr.append("Volume surge confirms")
            bs=int(bs*smult); ss=int(ss*smult)
            try:
                h1=self.client.get_candles(instrument,granularity="H1",count=20)
                h1rsi=calc_rsi([c["close"] for c in h1],14)
                if h1rsi and bs>ss and h1rsi<50: bs+=10; br.append(f"H1 RSI {h1rsi:.0f} confirms bullish")
                if h1rsi and ss>bs and h1rsi>50: ss+=10; sr.append(f"H1 RSI {h1rsi:.0f} confirms bearish")
            except Exception: pass
            signal="WAIT"; confidence=0; entry=sl=tp=None
            reasons=[f"RSI {rsi:.1f} neutral" if rsi else "RSI neutral",
                     "No H4 confluence",f"Session: {session}","Wait for setup"]
            if bs>=45 and bs>ss:
                signal="BUY"; confidence=min(95,bs); reasons=br
                try: entry=self.client.get_live_price(instrument)["ask"]
                except: entry=price
                if atr: sl=round(entry-atr*1.5,5); tp=round(entry+atr*2.5,5)
            elif ss>=45 and ss>bs:
                signal="SELL"; confidence=min(95,ss); reasons=sr
                try: entry=self.client.get_live_price(instrument)["bid"]
                except: entry=price
                if atr: sl=round(entry+atr*1.5,5); tp=round(entry-atr*2.5,5)
            return {"instrument":instrument,"timeframe":"H4","signal":signal,
                    "confidence":confidence,"price":round(price,6),
                    "entry":round(entry,6) if entry else None,
                    "sl":round(sl,6) if sl else None,
                    "tp":round(tp,6) if tp else None,
                    "sl_pips":round(atr*1.5/pip,1) if atr else None,
                    "tp_pips":round(atr*2.5/pip,1) if atr else None,
                    "rsi":rsi,"ema20":ema20,"ema50":ema50,"ema200":ema200,
                    "atr":atr,"macd":macd,"trend":trend,"session":session,
                    "vol_surge":vol_surge,
                    "donchian_high":round(don_high,5) if don_high else None,
                    "donchian_low":round(don_low,5) if don_low else None,
                    "reasons":reasons[:4],"candle_count":len(candles)}
        except Exception as e:
            logger.error(f"Signal error {instrument}: {e}")
            return {"instrument":instrument,"signal":"ERROR",
                    "error":str(e),"reasons":[str(e)],"confidence":0}
