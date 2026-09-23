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
 * Split a price path into up/down area data.
 * Shared hinge points keep segments continuous; whitespace gaps stop LWC
 * from drawing a line across the opposite-slope periods.
 */
function splitBySlope(points) {
  const upVals = new Map();
  const downVals = new Map();
  if (!points.length) {
    return { upData: [], downData: [] };
  }
  if (points.length === 1) {
    upVals.set(points[0].time, points[0].value);
  } else {
    let dir = null;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1];
      const b = points[i];
      const nextDir = b.value >= a.value ? "up" : "down";
      const bag = nextDir === "up" ? upVals : downVals;
      if (dir !== nextDir) {
        bag.set(a.time, a.value);
        dir = nextDir;
      }
      bag.set(b.time, b.value);
    }
  }

  const times = points.map((p) => p.time);
  const toSeries = (map) =>
    times.map((t) => (map.has(t) ? { time: t, value: map.get(t) } : { time: t }));

  return { upData: toSeries(upVals), downData: toSeries(downVals) };
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

  const upSeries = chart.addAreaSeries({
    ...UP_STYLE,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });

  const downSeries = chart.addAreaSeries({
    ...DOWN_STYLE,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });

  // Full path: markers + crosshair + last-price label (color follows latest slope).
  const markerSeries = chart.addLineSeries({
    color: UP_STYLE.lineColor,
    lineWidth: 0,
    priceLineVisible: false,
    lastValueVisible: true,
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

    const { upData, downData } = splitBySlope(priceData);
    upSeries.setData(upData);
    downSeries.setData(downData);
    markerSeries.setData(priceData);
    bleedSeries.setData(bleedData);
    neutralLine.setData(neutralData);
    volumeSeries.setData(volData);

    // Last-price badge follows the latest slope color.
    if (priceData.length >= 2) {
      const a = priceData[priceData.length - 2];
      const b = priceData[priceData.length - 1];
      const rising = b.value >= a.value;
      const c = rising ? UP_STYLE.lineColor : DOWN_STYLE.lineColor;
      markerSeries.applyOptions({
        color: c,
        crosshairMarkerBorderColor: c,
        crosshairMarkerBackgroundColor: c,
      });
    } else {
      markerSeries.applyOptions({
        color: UP_STYLE.lineColor,
        crosshairMarkerBorderColor: UP_STYLE.lineColor,
        crosshairMarkerBackgroundColor: UP_STYLE.lineColor,
      });
    }

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

    return { warmEnd, barCount: bars.length };
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
