/** @typedef {import('./api.js')} */

const BRT_ZONE = "America/Sao_Paulo";

const UP_STYLE = {
  lineColor: "#2ecc71",
  topColor: "rgba(46, 204, 113, 0.35)",
  bottomColor: "rgba(46, 204, 113, 0.02)",
};

const DOWN_STYLE = {
  lineColor: "#e74c3c",
  topColor: "rgba(231, 76, 60, 0.35)",
  bottomColor: "rgba(231, 76, 60, 0.02)",
};

function formatInBrt(unixSeconds, opts) {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: BRT_ZONE,
    ...opts,
  }).format(new Date(unixSeconds * 1000));
}

/** Axis / crosshair labels in Brasília time (UTC−3). */
function formatChartTime(time) {
  if (typeof time !== "number") return String(time);
  return formatInBrt(time, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatTickMark(time) {
  if (typeof time !== "number") return String(time);
  return formatInBrt(time, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/**
 * Break a price path into contiguous slope runs.
 * Each run becomes its own area series so green and red coexist on one chart.
 * Hinge points are shared so the path stays continuous at turns.
 */
function slopeSegments(points) {
  if (!points.length) return [];
  if (points.length === 1) return [{ dir: "up", points: [points[0]] }];

  const segs = [];
  let dir = points[1].value >= points[0].value ? "up" : "down";
  let cur = [points[0], points[1]];

  for (let i = 2; i < points.length; i++) {
    const nextDir = points[i].value >= points[i - 1].value ? "up" : "down";
    if (nextDir === dir) {
      cur.push(points[i]);
    } else {
      segs.push({ dir, points: cur });
      cur = [points[i - 1], points[i]];
      dir = nextDir;
    }
  }
  segs.push({ dir, points: cur });
  return segs;
}

export function createMeiaChart(container) {
  const LC = window.LightweightCharts;
  if (!LC) throw new Error("lightweight-charts not loaded");

  const chart = LC.createChart(container, {
    layout: {
      background: { color: "transparent" },
      textColor: "#70a080",
      fontFamily: "'JetBrains Mono', monospace",
    },
    grid: {
      vertLines: { color: "rgba(46,204,113,0.08)" },
      horzLines: { color: "rgba(46,204,113,0.08)" },
    },
    rightPriceScale: { borderColor: "rgba(46,204,113,0.25)" },
    timeScale: {
      borderColor: "rgba(46,204,113,0.25)",
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: formatTickMark,
    },
    localization: {
      locale: "pt-BR",
      timeFormatter: formatChartTime,
    },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });

  /** @type {ReturnType<typeof chart.addAreaSeries>[]} */
  let slopeSeries = [];

  // Invisible full path — only for buy markers + crosshair (never paints the line).
  const markerSeries = chart.addLineSeries({
    color: "rgba(0,0,0,0)",
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: true,
    crosshairMarkerRadius: 4,
    crosshairMarkerBorderColor: UP_STYLE.lineColor,
    crosshairMarkerBackgroundColor: UP_STYLE.lineColor,
  });

  const bleedSeries = chart.addLineSeries({
    color: "rgba(127, 255, 0, 0.55)",
    lineWidth: 1,
    lineStyle: LC.LineStyle.Dotted,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });

  const neutralLine = chart.addLineSeries({
    color: "rgba(224, 255, 224, 0.35)",
    lineWidth: 1,
    lineStyle: LC.LineStyle.Solid,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "vol",
    color: "rgba(46, 204, 113, 0.45)",
  });
  chart.priceScale("vol").applyOptions({
    scaleMargins: { top: 0.8, bottom: 0 },
  });
  chart.priceScale("right").applyOptions({
    scaleMargins: { top: 0.05, bottom: 0.25 },
  });

  function clearSlopeSeries() {
    for (const s of slopeSeries) {
      try {
        chart.removeSeries(s);
      } catch {
        /* already removed */
      }
    }
    slopeSeries = [];
  }

  function toUnix(iso) {
    return Math.floor(new Date(iso).getTime() / 1000);
  }

  function render(seriesPayload) {
    const bars = seriesPayload.bars || [];
    const priceData = [];
    const bleedData = [];
    const neutralData = [];
    const volData = [];
    let warmEnd = null;

    for (const b of bars) {
      const t = toUnix(b.t);
      priceData.push({ time: t, value: b.price });
      bleedData.push({ time: t, value: b.pure_bleed });
      neutralData.push({ time: t, value: 100 });
      volData.push({
        time: t,
        value: b.volume_bought_min || 0,
        color: (b.volume_bought_min || 0) > 0 ? "rgba(46,204,113,0.55)" : "rgba(112,160,128,0.25)",
      });
      if (b.warming_up) warmEnd = t;
    }

    clearSlopeSeries();
    const segs = slopeSegments(priceData);
    segs.forEach((seg, idx) => {
      const style = seg.dir === "up" ? UP_STYLE : DOWN_STYLE;
      const isLast = idx === segs.length - 1;
      const series = chart.addAreaSeries({
        ...style,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: isLast,
        crosshairMarkerVisible: false,
      });
      series.setData(seg.points);
      slopeSeries.push(series);
    });

    markerSeries.setData(priceData);
    if (segs.length) {
      const last = segs[segs.length - 1];
      const c = last.dir === "up" ? UP_STYLE.lineColor : DOWN_STYLE.lineColor;
      markerSeries.applyOptions({
        crosshairMarkerBorderColor: c,
        crosshairMarkerBackgroundColor: c,
      });
    }

    bleedSeries.setData(bleedData);
    neutralLine.setData(neutralData);
    volumeSeries.setData(volData);

    const markers = (seriesPayload.markers || []).map((m) => ({
      time: toUnix(m.t),
      position: "belowBar",
      color: "#7fff00",
      shape: "arrowUp",
      text: `${(m.granted_seconds / 60).toFixed(0)}m`,
    }));
    markerSeries.setMarkers(markers);

    if (warmEnd != null && priceData.length) {
      chart.timeScale().setVisibleRange({
        from: priceData[0].time,
        to: priceData[priceData.length - 1].time,
      });
    } else if (priceData.length) {
      chart.timeScale().fitContent();
    }

    return { warmEnd, barCount: bars.length, segments: segs.length };
  }

  function resize() {
    chart.applyOptions({
      width: container.clientWidth,
      height: container.clientHeight,
    });
  }

  resize();
  window.addEventListener("resize", resize);

  return { chart, render, resize };
}
