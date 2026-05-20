#!/usr/bin/env python3
# =====================================================
# ELEVATE V2.5 - WEB VERSION (Streamlit)
# Select Pair & Get Signal Instantly
# =====================================================

import streamlit as st
import asyncio
import statistics
from collections import deque
from datetime import datetime
import time
import threading

from pyquotex.stable_api import Quotex

# ====================== CONFIG ======================
QUOTEX_EMAIL = "necoweh783@tkonu.com"
QUOTEX_PASSWORD = "necoweh783@tkonu"
USE_DEMO = True

PAIRS = [
    "USDINR_otc", "NZDUSD_otc", "USDMXN_otc", "USDZAR_otc",
    "NZDCHF_otc", "USDBDT_otc", "CADCHF_otc", "USDCOP_otc",
    "USDPHP_otc", "USDPKR_otc", "USDIDR_otc", "GBPNZD_otc",
]

SIGNAL_COOLDOWN = 8
MIN_ATR_THRESHOLD = 0.00013
MIN_CONFLUENCE = 5

# ====================== SESSION STATE ======================
if "price_history" not in st.session_state:
    st.session_state.price_history = {pair: deque(maxlen=600) for pair in PAIRS}

if "quotex_client" not in st.session_state:
    st.session_state.quotex_client = None

if "connected" not in st.session_state:
    st.session_state.connected = False

if "last_signal_time" not in st.session_state:
    st.session_state.last_signal_time = 0

price_history = st.session_state.price_history

# ====================== INDICATORS ======================
def calculate_ema(prices, period):
    if len(prices) < period: 
        return None
    prices = list(prices)
    ema = statistics.mean(prices[:period])
    multiplier = 2 / (period + 1)
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 6)


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: 
        return 50.0
    gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period or 0.0001

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_atr(prices, period=14):
    if len(prices) < period + 1: 
        return 0.0
    trs = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    return sum(trs[-period:]) / period


def calculate_macd(prices):
    ema_fast = calculate_ema(prices, 12)
    ema_slow = calculate_ema(prices, 26)
    if None in (ema_fast, ema_slow):
        return 0.0, 0.0, 0.0
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(list(prices)[-18:], 9) or macd_line
    histogram = macd_line - signal_line
    return round(macd_line, 6), round(signal_line, 6), round(histogram, 6)


def calculate_bollinger_bands(prices, period=20, std_dev=2):
    if len(prices) < period: 
        return None, None, None
    p = list(prices)[-period:]
    sma = statistics.mean(p)
    variance = sum((x - sma) ** 2 for x in p) / period
    std = variance ** 0.5
    upper = round(sma + std_dev * std, 6)
    lower = round(sma - std_dev * std, 6)
    return upper, round(sma, 6), lower


def liquidity_proxy(prices):
    if len(prices) < 40: 
        return 0.0
    moves = [abs(prices[i] - prices[i-1]) for i in range(-40, 0)]
    return round(statistics.mean(moves) * 10000, 2)


def market_structure(prices):
    if len(prices) < 50: 
        return "NEUTRAL"
    if prices[-1] > prices[-25] and prices[-12] > prices[-35]:
        return "UPTREND"
    if prices[-1] < prices[-25] and prices[-12] < prices[-35]:
        return "DOWNTREND"
    return "NEUTRAL"


def detect_order_blocks(prices):
    if len(prices) < 50: 
        return None, None
    p = list(prices)
    bull_ob = bear_ob = None
    for i in range(-40, -10):
        if p[i] == min(p[i-8:i+5]) and p[i+8] > p[i]:
            bull_ob = p[i]
            break
    for i in range(-40, -10):
        if p[i] == max(p[i-8:i+5]) and p[i+8] < p[i]:
            bear_ob = p[i]
            break
    return bull_ob, bear_ob


def detect_fvg(prices):
    if len(prices) < 12: 
        return None
    p = list(prices)
    for i in range(-10, -2):
        gap = abs(p[i+1] - p[i-1])
        if gap > abs(p[i] - p[i-1]) * 1.4:
            if p[i+1] > p[i-1] and p[i+2] > p[i-1]:
                return "BULLISH_FVG"
            if p[i+1] < p[i-1] and p[i+2] < p[i-1]:
                return "BEARISH_FVG"
    return None


def exhaustion_filter(prices):
    if len(prices) < 20: 
        return "OK"
    ups = sum(1 for i in range(-18, -1) if prices[i] > prices[i-1])
    if ups >= 13: 
        return "BUY_EXHAUSTED"
    if ups <= 4: 
        return "SELL_EXHAUSTED"
    return "OK"


def calculate_confidence(confluence, rsi, liquidity):
    base = confluence * 16
    rsi_score = max(0, 12 - abs(rsi - 50) * 0.25)
    liq_score = min(liquidity / 10, 12)
    return round(min(base + rsi_score + liq_score, 94), 1)


# ====================== SIGNAL ENGINE ======================
def generate_signal(selected_pair):
    current_time = time.time()
    if current_time - st.session_state.last_signal_time < SIGNAL_COOLDOWN:
        return None, "⏳ Please wait a few seconds before requesting new signal"

    try:
        prices = list(price_history[selected_pair])
        if len(prices) < 400:
            return None, "📊 Not enough data yet. Please wait while collecting prices..."

        atr = calculate_atr(prices)
        if atr < MIN_ATR_THRESHOLD:
            return None, f"ATR too low ({atr})"

        rsi = calculate_rsi(prices)
        liquidity = liquidity_proxy(prices)
        structure = market_structure(prices)
        exhaustion = exhaustion_filter(prices)

        if exhaustion != "OK":
            return None, f"Market exhausted: {exhaustion}"

        bull_ob, bear_ob = detect_order_blocks(prices)
        fvg = detect_fvg(prices)

        confluence = 0
        reasons = []

        ema5 = calculate_ema(prices, 5)
        ema10 = calculate_ema(prices, 10)
        ema20 = calculate_ema(prices, 20)

        if ema5 and ema10 and ema20:
            if ema5 > ema10 > ema20 and structure == "UPTREND":
                confluence += 2
                reasons.append("EMA Bullish Alignment")
            elif ema5 < ema10 < ema20 and structure == "DOWNTREND":
                confluence += 2
                reasons.append("EMA Bearish Alignment")

        macd_line, macd_sig, macd_hist = calculate_macd(prices)
        if macd_hist > 0 and structure == "UPTREND" or macd_hist < 0 and structure == "DOWNTREND":
            confluence += 1
            reasons.append("MACD Confirmation")

        upper, mid, lower = calculate_bollinger_bands(prices)
        current = prices[-1]
        if mid and ((current < mid and structure == "UPTREND") or (current > mid and structure == "DOWNTREND")):
            confluence += 1
            reasons.append("Bollinger Reversion")

        if bull_ob and structure == "UPTREND" and abs(current - bull_ob)/bull_ob < 0.0045:
            confluence += 1
            reasons.append("Bullish Order Block")
        if bear_ob and structure == "DOWNTREND" and abs(current - bear_ob)/bear_ob < 0.0045:
            confluence += 1
            reasons.append("Bearish Order Block")

        if fvg == "BULLISH_FVG" and structure == "UPTREND":
            confluence += 1
            reasons.append("Bullish FVG")
        elif fvg == "BEARISH_FVG" and structure == "DOWNTREND":
            confluence += 1
            reasons.append("Bearish FVG")

        if liquidity > 8:
            confluence += 1
            reasons.append("High Liquidity")

        if confluence < MIN_CONFLUENCE:
            return None, f"Low Confluence ({confluence}/{MIN_CONFLUENCE})"

        direction = "CALL" if structure == "UPTREND" else "PUT"
        confidence = calculate_confidence(confluence, rsi, liquidity)

        signal_data = {
            "pair": selected_pair,
            "signal": "🟢 CALL | BUY" if direction == "CALL" else "🔴 PUT | SELL",
            "strength": "STRONG" if confluence >= 6 else "GOOD",
            "confluence": confluence,
            "confidence": confidence,
            "reasons": " | ".join(reasons),
            "entry": round(current, 5),
            "sl": round(current - atr*1.7 if direction == "CALL" else current + atr*1.7, 5),
            "tp": round(current + atr*3.0 if direction == "CALL" else current - atr*3.0, 5),
            "entry_time": datetime.now().strftime("%H:%M:%S"),
            "direction": direction
        }

        st.session_state.last_signal_time = current_time
        return signal_data, None

    except Exception as e:
        return None, f"Error: {str(e)}"


# ====================== BACKGROUND PRICE UPDATER ======================
def background_price_updater():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def updater():
        while True:
            try:
                if st.session_state.quotex_client and st.session_state.connected:
                    for pair in PAIRS:
                        await st.session_state.quotex_client.start_realtime_price(pair)
                        realtime = await st.session_state.quotex_client.get_realtime_price(pair)
                        if realtime and len(realtime) > 0:
                            latest = float(realtime[-1]["price"])
                            price_history[pair].append(latest)
                await asyncio.sleep(0.5)
            except:
                await asyncio.sleep(2)

    loop.run_until_complete(updater())


# ====================== STREAMLIT UI ======================
st.set_page_config(page_title="ELEVATE V2.5", page_icon="🚀", layout="wide")

st.title("🚀 ELEVATE V2.5")
st.markdown("**Smart Confluence Trading Signals**")

# Sidebar
with st.sidebar:
    st.header("Connection")
    if not st.session_state.connected:
        if st.button("🔗 Connect to Quotex", type="primary"):
            with st.spinner("Connecting..."):
                try:
                    client = Quotex(email=QUOTEX_EMAIL, password=QUOTEX_PASSWORD)
                    connected, reason = asyncio.run(client.connect())
                    if connected:
                        asyncio.run(client.change_account("PRACTICE" if USE_DEMO else "REAL"))
                        st.session_state.quotex_client = client
                        st.session_state.connected = True
                        threading.Thread(target=background_price_updater, daemon=True).start()
                        st.success("✅ Connected Successfully!")
                        st.rerun()
                    else:
                        st.error(f"❌ {reason}")
                except Exception as e:
                    st.error(f"Connection Error: {e}")
    else:
        st.success("✅ Connected (Practice Mode)")

    st.divider()
    st.caption(f"Data Points: {len(price_history[list(price_history.keys())[0]])}")

# Main Area
col1, col2 = st.columns([3, 1])

with col1:
    selected_pair = st.selectbox("**Select Trading Pair**", PAIRS, index=0)

    if st.button("📡 GENERATE SIGNAL", type="primary", use_container_width=True):
        with st.spinner("Analyzing market with multiple indicators..."):
            signal_data, error = generate_signal(selected_pair)

            if error:
                st.error(error)
            else:
                st.success("✅ Signal Generated!")

                signal_color = "🟢" if "CALL" in signal_data['signal'] else "🔴"
                
                st.markdown(f"### {signal_data['pair']}")
                st.markdown(f"# {signal_color} **{signal_data['signal']}**")

                st.metric(label="Confidence", value=f"{signal_data['confidence']}%", 
                         delta=f"Confluence: {signal_data['confluence']}/8")

                st.info(f"**Reasons:** {signal_data['reasons']}")

                c1, c2, c3 = st.columns(3)
                c1.metric("Entry Price", signal_data['entry'])
                c2.metric("Stop Loss", signal_data['sl'])
                c3.metric("Take Profit", signal_data['tp'])

                st.caption(f"Time: {signal_data['entry_time']}")

with col2:
    st.subheader("Live Price")
    current_price = list(price_history[selected_pair])[-1] if price_history[selected_pair] else "Waiting for data..."
    st.markdown(f"**{current_price}**" if isinstance(current_price, float) else current_price)

    st.caption("Price updates automatically in background")

st.divider()
st.caption("ELEVATE V2.5 Web | Built with your original strategy")

# Auto refresh
if st.session_state.connected:
    time.sleep(3)
    st.rerun()