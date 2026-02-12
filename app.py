import streamlit as st
import pyupbit
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime
import requests

# 1. 페이지 설정
st.set_page_config(page_title="고래 스캐너 v2.8.1", layout="wide")

# --- 유틸리티: 가격 포맷팅 ---
def format_price(price):
    if price >= 1000: return round(price)
    if price >= 100: return round(price, 1)
    return round(price, 2)

# --- 상승 확률 계산 엔진 ---
def calculate_rise_probability(df, curr_val, whale_limit_billion):
    score = 0
    vol_ratio = curr_val / (whale_limit_billion * 100)
    score += min(vol_ratio * 10, 40) 
    curr_rsi = df['rsi'].iloc[-1]
    if 35 <= curr_rsi <= 55: score += 30
    elif 55 < curr_rsi <= 65: score += 15
    ma5, ma20 = df['ma5'].iloc[-1], df['ma20'].iloc[-1]
    if ma5 > ma20: score += 30
    elif ma5 > df['ma5'].iloc[-2]: score += 10
    return int(score)

# --- 사이드바 설정 ---
st.sidebar.header("🚀 전략 및 예측 필터")
preset = st.sidebar.radio("모드 선택", ("사용자 지정", "안정형 (확률 우선)", "공격형 (화력 우선)", "단기 낙주 매매"))

if preset == "안정형 (확률 우선)":
    default_rsi, default_whale, default_prob = 45, 1.5, 75
elif preset == "공격형 (화력 우선)":
    default_rsi, default_whale, default_prob = 60, 5.0, 50
elif preset == "단기 낙주 매매":
    default_rsi, default_whale, default_prob = 30, 3.0, 70
else:
    default_rsi, default_whale, default_prob = 45, 2.0, 60

RSI_THRESHOLD = st.sidebar.slider("RSI 기준", 10, 75, default_rsi)
WHALE_LIMIT_BILLION = st.sidebar.number_input("1분 거래액 기준(억)", value=default_whale, step=0.1)
MIN_PROB_THRESHOLD = st.sidebar.slider("최소 예측 확률 필터 (%)", 30, 95, default_prob, 5)
USE_GOLDEN_CROSS = st.sidebar.toggle("🔔 골든크로스(5/20) 필수", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("🚫 제외 종목 (블랙리스트)")
all_krw_tickers = pyupbit.get_tickers(fiat="KRW")
exclude_list = st.sidebar.multiselect("스캔 제외 종목", options=all_krw_tickers, default=["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-USDT"])

st.sidebar.subheader("🎯 수익/손실 설정")
user_tp_pct = st.sidebar.slider("목표 익절 (%)", 0.5, 10.0, 2.0, 0.5)
user_sl_pct = st.sidebar.slider("허용 손절 (%)", 0.5, 5.0, 1.5, 0.5)

DEFAULT_WEBHOOK = "https://discordapp.com/api/webhooks/1470912307084136459/e9nEv1oNisa1gHXjO2ny0dkD2RNsHF-FpvYQgjFZjkYcS9O9VA2XE0DjLmSeIibNbJBR"
DISCORD_WEBHOOK_URL = st.sidebar.text_input("디스코드 웹훅", value=DEFAULT_WEBHOOK, type="password")

if st.sidebar.button("🗑️ 모든 기록 초기화"):
    st.session_state.signals, st.session_state.recent_detected, st.session_state.last_alert_time = [], [], {}
    st.rerun()

if 'signals' not in st.session_state: st.session_state.signals = []
if 'recent_detected' not in st.session_state: st.session_state.recent_detected = []
if 'last_alert_time' not in st.session_state: st.session_state.last_alert_time = {}

@st.cache_data(ttl=600)
def get_top_tickers(count, blacklist):
    try:
        tickers = [t for t in pyupbit.get_tickers(fiat="KRW") if t not in blacklist]
        prices = pyupbit.get_current_price(tickers, verbose=True)
        top_df = pd.DataFrame(prices)
        return top_df.sort_values(by='acc_trade_price_24h', ascending=False).head(count)['market'].tolist()
    except: return []

def send_discord_message(msg):
    if DISCORD_WEBHOOK_URL:
        try: requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=5)
        except: pass

st.title("🐳 고래 스캐너 v2.8.1")
tickers_to_scan = get_top_tickers(100, exclude_list)
placeholder = st.empty()

while True:
    with placeholder.container():
        st.write(f"🔄 **전략 분석 중...** ({datetime.now().strftime('%H:%M:%S')})")
        progress_bar = st.progress(0)
        status_text = st.empty()
        all_current_prices = pyupbit.get_current_price(tickers_to_scan)

        for idx, ticker in enumerate(tickers_to_scan):
            try:
                status_text.text(f"🔍 스캔: {ticker} ({idx+1}/{len(tickers_to_scan)})")
                progress_bar.progress((idx + 1) / len(tickers_to_scan))
                curr_price = all_current_prices.get(ticker)
                if not curr_price: continue
                symbol = ticker.replace("KRW-", "")
                chart_url = f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"

                # 1. 익절/손절 알림 시에도 링크 포함
                for s in st.session_state.recent_detected:
                    if s['종목'] == symbol and s['상태'] == "⏳ 감시중":
                        if curr_price >= s['raw_tp']:
                            s['상태'] = "✅ 익절 완료"
                            send_discord_message(f"🎯 **[익절] {symbol}** 목표 달성!\n🔗 [차트 바로가기]({chart_url})")
                        elif curr_price <= s['raw_sl']:
                            s['상태'] = "❌ 손절 완료"
                            send_discord_message(f"📉 **[손절] {symbol}** 지지선 이탈\n🔗 [차트 바로가기]({chart_url})")

                df = pyupbit.get_ohlcv(ticker, interval="minute1", count=40)
                if df is None or len(df) < 21: continue
                df['rsi'] = ta.rsi(df['close'], length=14)
                df['ma5'], df['ma20'] = df['close'].rolling(5).mean(), df['close'].rolling(20).mean()
                df['range'] = df['high'] - df['low']
                avg_range = df['range'].iloc[-10:].mean()
                curr_rsi, curr_val = df['rsi'].iloc[-1], (curr_price * df['volume'].iloc[-1]) / 1_000_000
                is_gc = (df['ma5'].iloc[-2] <= df['ma20'].iloc[-2]) and (df['ma5'].iloc[-1] > df['ma20'].iloc[-1])

                if (not USE_GOLDEN_CROSS or is_gc) and (curr_rsi <= RSI_THRESHOLD) and (curr_val >= (WHALE_LIMIT_BILLION * 100)):
                    prob_score = calculate_rise_probability(df, curr_val, WHALE_LIMIT_BILLION)
                    
                    if prob_score >= MIN_PROB_THRESHOLD:
                        tp_raw = max(curr_price * (1 + user_tp_pct/100), curr_price + (avg_range * 2))
                        sl_raw = min(curr_price * (1 - user_sl_pct/100), curr_price - (avg_range * 1.5))
                        final_tp, final_sl = format_price(tp_raw), format_price(sl_raw)
                        tp_pct = ((final_tp - curr_price) / curr_price) * 100
                        
                        sig_data = {
                            "시간": datetime.now().strftime("%H:%M:%S"), "종목": symbol, 
                            "확률": f"{prob_score}%", "현재가": f"{curr_price:,}",
                            "익절가": f"{final_tp:,.1f} ({tp_pct:+.1f}%)", 
                            "상태": "⏳ 감시중", "raw_tp": final_tp, "raw_sl": final_sl, "raw_time": time.time(),
                            "차트": chart_url
                        }
                        
                        if time.time() - st.session_state.last_alert_time.get(ticker, 0) > 300:
                            st.session_state.signals.insert(0, sig_data)
                            st.session_state.recent_detected.insert(0, sig_data)
                            st.session_state.last_alert_time[ticker] = time.time()
                            
                            emoji = "🔥" if prob_score >= 85 else "⚡"
                            # ✅ 디스코드 메시지에 마크다운 링크 추가
                            msg = (f"{emoji} **[전략 포착] {symbol}**\n"
                                   f"📈 상승 확률: **{prob_score}%**\n"
                                   f"💰 진입: {curr_price:,}원\n"
                                   f"🎯 목표: {sig_data['익절가']}\n"
                                   f"🔗 **[업비트 차트 열기]({chart_url})**")
                            send_discord_message(msg)
            except: continue
            time.sleep(0.04)

        st.session_state.recent_detected = [s for s in st.session_state.recent_detected if time.time() - s['raw_time'] < 600 or s['상태'] == "⏳ 감시중"]

        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("🔥 실시간 추적")
            for s in st.session_state.recent_detected[:8]:
                color = "green" if "익절" in s['상태'] else "red" if "손절" in s['상태'] else "blue"
                with st.container(border=True):
                    st.markdown(f"### {s['종목']} (확률: {s['확률']})")
                    st.write(f"진입: **{s['현재가']}** → 목표: **{s['익절가']}**")
                    st.link_button(f"{s['종목']} 차트", s['차트'])
        with col2:
            st.subheader("📜 히스토리")
            if st.session_state.signals:
                st.dataframe(pd.DataFrame(st.session_state.signals)[["시간", "종목", "확률", "현재가", "상태"]].head(20), use_container_width=True, hide_index=True)
    time.sleep(1)
