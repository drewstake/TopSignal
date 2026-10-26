// TopBot Mathematical — frozen Bayesian model signals, FXReplay V2.
// Paste the GENERATED file, not this template. Use MNQ, 5-minute candles.
// Observation model only: no broker/account gates, orders or performance claims.
// BUY/SELL are fresh model proposals on EACH qualifying closed candle.
// Stop/target guides use that candle's close, not an actual execution fill.
// Inspect the next bar's marker: only bars 1..21 enter the calculation.
// Model metadata and paths are embedded by export_topbot_fxreplay.py.
indicator({ onMainPanel: true, format: 'inherit' });
const showGuides = input.bool('Show stop / target / 15 minute guides', true);
const showStatus = input.bool('Show current status', true);
// This TA call declares the history requirement to FXReplay's engine.
const historyRequest = ta.sma(close, 22);
var model = __TOPBOT_MODEL__;
var lastProcessedTime = null;

// Pure computation shared with the Python-versus-JavaScript parity check.
function topbotFeatures(rows) {
  if (rows.length !== 21) return null;
  let sumSq = 0, recentSq = 0, momentum = 0, volumeSum = 0;
  for (let i = 0; i < 21; i++) {
    const b = rows[i];
    if (![b.o, b.h, b.l, b.c, b.v, b.t].every(Number.isFinite) ||
        Math.min(b.o, b.h, b.l, b.c, b.v) <= 0 ||
        b.l > Math.min(b.o, b.c) || b.h < Math.max(b.o, b.c) ||
        b.t % 300 !== 0 || (i > 0 && b.t - rows[i - 1].t !== 300)) return null;
    if (i < 20) volumeSum += b.v;
    if (i > 0) {
      const change = b.c - rows[i - 1].c;
      sumSq += change * change;
      if (i >= 16) recentSq += change * change;
      if (i >= 18) momentum += change;
    }
  }
  const sigma = Math.sqrt(sumSq / 20), recent = Math.sqrt(recentSq / 5);
  if (!(sigma > 0 && recent > 0)) return null;
  const values = [(rows[20].c - rows[19].c) / sigma,
    momentum / (sigma * Math.sqrt(3)), Math.log(recent / sigma),
    Math.log(rows[20].v / (volumeSum / 20))];
  if (!values.every(Number.isFinite) || values.some(x => Math.abs(x) > 50)) return null;
  const stop = Math.max(4, Math.ceil(sigma * Math.sqrt(3) / 0.25) * 0.25);
  return { sigma: sigma, values: values, stop: stop,
    target: Math.ceil(stop * 1.5 / 0.25) * 0.25,
    cell: (values[1] >= 0 ? 2 : 0) + (values[2] >= 0 ? 1 : 0) };
}

function topbotPath(m, i) {
  return m.pathChunks[Math.floor(i / m.pathChunkSize)][i % m.pathChunkSize];
}

// Lossless model decoding. Each scale is its original IEEE754 double; each
// path price is its original integer tick displacement divided by that scale.
function topbotByte(encoded, cursor) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const index = cursor.position++;
  const group = Math.floor(index / 3) * 4;
  const word = (alphabet.indexOf(encoded.charAt(group)) << 18) |
    (alphabet.indexOf(encoded.charAt(group + 1)) << 12) |
    (Math.max(0, alphabet.indexOf(encoded.charAt(group + 2))) << 6) |
    Math.max(0, alphabet.indexOf(encoded.charAt(group + 3)));
  return (word >>> ((2 - index % 3) * 8)) & 255;
}

function topbotUint(encoded, cursor) {
  let value = 0, multiplier = 1, b = 0;
  do {
    b = topbotByte(encoded, cursor);
    value += (b & 127) * multiplier;
    multiplier *= 128;
  } while (b >= 128);
  return value;
}

function topbotDecodeModel(m) {
  const cursor = { position: 0 }, encoded = m.encodedPaths;
  const chunks = [];
  let chunk = [];
  for (let i = 0; i < m.pathCount; i++) {
    const cell = topbotUint(encoded, cursor), day = topbotUint(encoded, cursor);
    const bytes = [];
    for (let j = 0; j < 8; j++) bytes.push(topbotByte(encoded, cursor));
    const exponent = (bytes[7] & 127) * 16 + (bytes[6] >>> 4);
    const mantissa = (bytes[6] & 15) * 281474976710656 + bytes[5] * 1099511627776 +
      bytes[4] * 4294967296 + bytes[3] * 16777216 + bytes[2] * 65536 + bytes[1] * 256 + bytes[0];
    const norm = (1 + mantissa / 4503599627370496) * Math.pow(2, exponent - 1023);
    const row = [cell, day];
    for (let j = 0; j < 12; j++) {
      const unsigned = topbotUint(encoded, cursor);
      const tick = unsigned % 2 === 0 ? unsigned / 2 : -(unsigned + 1) / 2;
      row.push(tick * 0.25 / norm);
    }
    chunk.push(row);
    if (chunk.length === m.pathChunkSize || i === m.pathCount - 1) {
      chunks.push(chunk);
      chunk = [];
    }
  }
  m.pathChunks = chunks;
  return m;
}

function topbotForecast(m, f) {
  const n = m.pathCount, state = m.states[f.cell];
  const scale = f.sigma * Math.sqrt(3);
  const output = {
    BUY: { mean: 0, lower: 0, se: 0 },
    SELL: { mean: 0, lower: 0, se: 0 },
    action: 'HOLD'
  };
  for (let sideIndex = 0; sideIndex < 2; sideIndex++) {
    const side = sideIndex === 0 ? 1 : -1;
    const action = side === 1 ? 'BUY' : 'SELL';
    const payoffs = [], weights = [];
    let mean = 0;
    for (let i = 0; i < n; i++) {
      const row = topbotPath(m, i);
      const base = ((row[0] === f.cell ? 1 : 0) + 20 / n) / (state.count + 20);
      const w = (1 - m.poolMix) * base + m.poolMix / n;
      // Entry and exit each include 0.5 adverse MNQ points; fees $1.22 RT.
      const entry = side * row[2] * scale + 0.5;
      const stopLevel = entry - f.stop, targetLevel = entry + f.target;
      let fill = side * row[13] * scale;
      for (let j = 0; j < 3; j++) {
        const k = 2 + j * 4, opening = side * row[k] * scale;
        const favorable = (side === 1 ? row[k + 1] : -row[k + 2]) * scale;
        const adverse = (side === 1 ? row[k + 2] : -row[k + 1]) * scale;
        if (opening <= stopLevel) { fill = opening; break; }
        if (opening >= targetLevel) { fill = targetLevel; break; }
        if (adverse <= stopLevel) { fill = Math.min(stopLevel, opening); break; }
        if (favorable >= targetLevel) { fill = targetLevel; break; }
      }
      const net = 2 * (fill - 0.5 - entry) - 1.22;
      weights.push(w); payoffs.push(net); mean += w * net;
    }
    const contributions = new Array(m.dayCount).fill(0);
    for (let i = 0; i < n; i++) contributions[topbotPath(m, i)[1]] += weights[i] * (payoffs[i] - mean);
    let variance = 0;
    for (let i = 0; i < contributions.length; i++) variance += contributions[i] * contributions[i];
    let hac = variance;
    for (let lag = 1; lag < Math.min(5, contributions.length); lag++) {
      let covariance = 0;
      for (let i = lag; i < contributions.length; i++) covariance += contributions[i] * contributions[i - lag];
      hac += 2 * (1 - lag / 5) * covariance;
    }
    const correction = state.effectiveDays / Math.max(1e-9, state.effectiveDays - 1);
    const se = Math.sqrt(Math.max(variance, hac, 0) * correction);
    output[action] = { mean: mean, lower: mean - state.critical * se - 1, se: se };
  }
  const best = output.BUY.lower >= output.SELL.lower ? 'BUY' : 'SELL';
  const supported = n >= 300 && state.effectiveDays >= 20 && f.stop >= 4 && f.stop <= 25;
  output.action = supported && Number.isFinite(output[best].lower) && output[best].lower > 1 ? best : 'HOLD';
  return output;
}

function topbotSeconds(t) { return t > 100000000000 ? t / 1000 : t; }

// Session rows are exported from TopBot's own timezone and holiday rules.
// UTC boundaries include DST and the 17-minute early-close entry buffer.
function topbotSession(m, t) {
  const day = Math.floor(t / 86400);
  return m.sessions[String(day)] || null;
}

const currentTime = time(0);
const currentSeconds = topbotSeconds(currentTime);
if (index >= 21 && currentTime !== lastProcessedTime && Number.isFinite(currentSeconds)) {
  lastProcessedTime = currentTime;
  const rows = [];
  for (let offset = 21; offset >= 1; offset--) {
    rows.push({ t: topbotSeconds(time(offset)), o: open(offset), h: high(offset),
      l: low(offset), c: close(offset), v: volume(offset) });
  }
  const f = topbotFeatures(rows);
  const session = topbotSession(model, currentSeconds);
  let status = 'HOLD: outside entry session';
  let result = null;
  if (currentSeconds < model.earliestDecision) status = 'HOLD: before model training cutoff + embargo';
  else if (currentSeconds >= model.calendarEnd) status = 'HOLD: exported calendar expired';
  else if (!f || currentSeconds - rows[20].t !== 300) status = 'HOLD: need 21 complete contiguous 5m candles + volume';
  else if (session && currentSeconds >= session[0] && currentSeconds <= session[1]) {
    if (f.stop > 25) status = 'HOLD: implied stop ' + f.stop.toFixed(2) + ' exceeds 25 points';
    else {
      if (!model.pathChunks) topbotDecodeModel(model);
      result = topbotForecast(model, f);
      status = result.action === 'HOLD' ? 'HOLD: insufficient evidence or net edge' : result.action;
    }
  }
  const price = rows[20].c;
  if (result && result.action !== 'HOLD') {
    const side = result.action === 'BUY' ? 1 : -1;
    const signalColor = side === 1 ? '#00b87a' : '#ef5350';
    const marker = side === 1 ? rows[20].l - 1 : rows[20].h + 1;
    rectangle.create(time(0), marker + 0.5, bar(barIndex + 1), marker - 0.5, {
      color: signalColor, fillColor: side === 1 ? 'rgba(0,184,122,0.25)' : 'rgba(239,83,80,0.25)',
      text: result.action + ' | lower $' + result[result.action].lower.toFixed(2)
    });
    if (showGuides) {
      const stopPrice = price - side * f.stop, targetPrice = price + side * f.target;
      rectangle.create(time(0), Math.max(price, stopPrice), bar(barIndex + 3), Math.min(price, stopPrice), {
        color: '#ef5350', fillColor: 'rgba(239,83,80,0.08)', text: 'SL ' + stopPrice.toFixed(2)
      });
      rectangle.create(time(0), Math.max(price, targetPrice), bar(barIndex + 3), Math.min(price, targetPrice), {
        color: '#00b87a', fillColor: 'rgba(0,184,122,0.08)', text: 'TP ' + targetPrice.toFixed(2) + ' | 15m horizon'
      });
    }
  }
  if (showStatus && index === candlesCount - 1) {
    rectangle.create(time(1), price + 2, time(0), price, {
      color: '#78909c', fillColor: 'rgba(120,144,156,0.12)', text: 'TopBot frozen model | ' + status
    });
  }
}
