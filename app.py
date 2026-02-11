import streamlit as st
import pyupbit
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime
import requests

# 1. 페이지 설정
st.set_page_config(page_title="고래 스캐너 v2.6", layout="wide")

# --- 유틸리티: 가격 포맷팅 ---
def format_price(price):
    if price >= 1000: return round(price)
    if price >= 100: return round(price, 1)
    return round(price, 2)

# --- 사이드바 설정 ---
st.sidebar.header("🚀 전략 및 프리셋")
preset = st.sidebar.radio("모드 선택", ("사용자 지정", "현재 (추천) - 조용한 시장", "단기 급락장 - 낙주 매매", "불장 - 주도주 추격"))
USE_GOLDEN_CROSS = st.sidebar.toggle("🔔 골든크로스(5/20) 필수", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("🚫 제외 종목")
all_krw_tickers = pyupbit.get_tickers(fiat="KRW")
exclude_list = st.sidebar.multiselect("스캔 제외", options=all_krw_tickers, default=["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-USDT"])

st.sidebar.subheader("🎯 목표 수익/손실 (%)")
user_tp_pct = st.sidebar.slider("목표 익절 (%)", 0.5, 10.0, 2.0, 0.5)
user_sl_pct = st.sidebar.slider("허용 손절 (%)", 0.5, 5.0, 1.5, 0.5)

# 프리셋 로직
if preset == "현재 (추천) - 조용한 시장":
    default_min_price, default_rsi, default_whale = 10, 45, 1.5
elif preset == "단기 급락장 - 낙주 매매":
    default_min_price, default_rsi, default_whale = 100, 35, 3.0
elif preset == "불장 - 주도주 추격":
    default_min_price, default_rsi, default_whale = 1, 60, 10.0
else:
    default_min_price, default_rsi, default_whale = 10, 40, 5.0

st.sidebar.markdown("---")
MIN_PRICE = st.sidebar.number_input("최소 가격", value=default_min_price)
RSI_THRESHOLD = st.sidebar.slider("RSI 기준", 10, 75, default_rsi)
WHALE_LIMIT_BILLION = st.sidebar.number_input("1분 거래액 기준(억)", value=default_whale, step=0.1)
WHALE_KRW_LIMIT = WHALE_LIMIT_BILLION * 100 

# --- [수정] 디스코드 웹훅 기본값 설정 ---
DEFAULT_WEBHOOK = "https://discordapp.com/api/webhooks/1470912307084136459/e9nEv1oNisa1gHXjO2ny0dkD2RNsHF-FpvYQgjFZjkYcS9O9VA2XE0DjLmSeIibNbJBR"
DISCORD_WEBHOOK_URL = st.sidebar.text_input("디스코드 웹훅", value=DEFAULT_WEBHOOK, type="password")

if st.sidebar.button("🗑️ 모든 기록 초기화"):
    st.session_state.signals, st.session_state.recent_detected, st.session_state.last_alert_time = [], [], {}
    st.rerun()

# --- 세션 상태 및 로직 (v2.5와 동일) ---
if 'signals' not in st.session_state: st.session_state.signals = []
if 'recent_detected' not in st.session_state: st.session_state.recent_detected = []
if 'last_alert_time' not in st.session_state: st.session_state.last_alert_time = {}

@st.cache_data(ttl=600)
def get_top_tickers(count, min_price, blacklist):
    try:
        tickers = [t for t in pyupbit.get_tickers(fiat="KRW") if t not in blacklist]
        prices = pyupbit.get_current_price(tickers, verbose=True)
        top_df = pd.DataFrame(prices).query(f'trade_price >= {min_price}')
        return top_df.sort_values(by='acc_trade_price_24h', ascending=False).head(count)['market'].tolist()
    except: return []

def send_discord_message(msg):
    if DISCORD_WEBHOOK_URL:
        try: requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=5)
        except: pass

st.title("🐳 고래 스캐너 v2.6")
tickers_to_scan = get_top_tickers(100, MIN_PRICE, exclude_list)
placeholder = st.empty()

while True:
    with placeholder.container():
        st.write(f"🔄 **분석 및 추적 중...** (업데이트: {datetime.now().strftime('%H:%M:%S')})")
        progress_bar = st.progress(0)
        status_text = st.empty()
        all_current_prices = pyupbit.get_current_price(tickers_to_scan)

        for idx, ticker in enumerate(tickers_to_scan):
            try:
                status_text.text(f"🔍 분석 중: {ticker} ({idx+1}/{len(tickers_to_scan)})")
                progress_bar.progress((idx + 1) / len(tickers_to_scan))
                curr_price = all_current_prices.get(ticker)
                if not curr_price: continue
                symbol = ticker.replace("KRW-", "")

                for s in st.session_state.recent_detected:
                    if s['종목'] == symbol and s['상태'] == "⏳ 감시중":
                        if curr_price >= s['raw_tp']:
                            s['상태'] = "✅ 익절 완료"
                            send_discord_message(f"🎯 **[익절] {symbol}** 목표가 도달!\n진입: {s['현재가']} -> 현재: {curr_price:,}")
                        elif curr_price <= s['raw_sl']:
                            s['상태'] = "❌ 손절 완료"
                            send_discord_message(f"📉 **[손절] {symbol}** 손절가 도달!\n진입: {s['현재가']} -> 현재: {curr_price:,}")

                df = pyupbit.get_ohlcv(ticker, interval="minute1", count=40)
                if df is None or len(df) < 21: continue
                df['rsi'] = ta.rsi(df['close'], length=14)
                df['ma5'], df['ma20'] = df['close'].rolling(5).mean(), df['close'].rolling(20).mean()
                df['range'] = df['high'] - df['low']
                avg_range = df['range'].iloc[-10:].mean()
                curr_rsi, curr_val = df['rsi'].iloc[-1], (curr_price * df['volume'].iloc[-1]) / 1_000_000
                is_gc = (df['ma5'].iloc[-2] <= df['ma20'].iloc[-2]) and (df['ma5'].iloc[-1] > df['ma20'].iloc[-1])

                if (not USE_GOLDEN_CROSS or is_gc) and (curr_rsi <= RSI_THRESHOLD) and (curr_val >= WHALE_KRW_LIMIT):
                    tp_raw = max(curr_price * (1 + user_tp_pct/100), curr_price + (avg_range * 2))
                    sl_raw = min(curr_price * (1 - user_sl_pct/100), curr_price - (avg_range * 1.5))
                    final_tp, final_sl = format_price(tp_raw), format_price(sl_raw)
                    tp_pct, sl_pct = ((final_tp - curr_price) / curr_price) * 100, ((final_sl - curr_price) / curr_price) * 100
                    
                    sig_data = {
                        "시간": datetime.now().strftime("%H:%M:%S"), "종목": symbol, "현재가": f"{curr_price:,}",
                        "RSI": f"{curr_rsi:.1f}", "익절가": f"{final_tp:,.1f} ({tp_pct:+.1f}%)", 
                        "손절가": f"{final_sl:,.1f} ({sl_pct:+.1f}%)", "상태": "⏳ 감시중",
                        "raw_tp": final_tp, "raw_sl": final_sl, "raw_time": time.time(),
                        "차트": f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
                    }
                    if time.time() - st.session_state.last_alert_time.get(ticker, 0) > 300:
                        st.session_state.signals.insert(0, sig_data)
                        st.session_state.recent_detected.insert(0, sig_data)
                        st.session_state.last_alert_time[ticker] = time.time()
                        send_discord_message(f"🚨 **신규 포착: {symbol}**\n진입: {curr_price:,}원\n목표: {sig_data['익절가']}")
            except: continue
            time.sleep(0.04)

        st.session_state.recent_detected = [s for s in st.session_state.recent_detected if time.time() - s['raw_time'] < 600 or s['상태'] == "⏳ 감시중"]

        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("🔥 실시간 추적")
            for s in st.session_state.recent_detected[:8]:
                color = "green" if "익절" in s['상태'] else "red" if "손절" in s['상태'] else "blue"
                with st.container(border=True):
                    st.markdown(f"### {s['종목']} :{color}[{s['상태']}]")
                    st.write(f"진입: **{s['현재가']}** → 목표: **{s['익절가']}**")
                    st.link_button(f"{s['종목']} 차트", s['차트'])
        with col2:
            st.subheader("📜 신호 히스토리")
            if st.session_state.signals:
                st.dataframe(pd.DataFrame(st.session_state.signals)[["시간", "종목", "현재가", "상태"]].head(20), use_container_width=True, hide_index=True)
    time.sleep(1)