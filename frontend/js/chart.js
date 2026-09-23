/** @typedef {import('./api.js')} */

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
    timeScale: { borderColor: "rgba(46,204,113,0.25)", timeVisible: true },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });

  const priceSeries = chart.addAreaSeries({
    lineColor: "#2ecc71",
    topColor: "rgba(46, 204, 113, 0.35)",
    bottomColor: "rgba(46, 204, 113, 0.02)",
    lineWidth: 2,
    priceLineVisible: false,
  });

  const bleedSeries = chart.addLineSeries({
    color: "rgba(127, 255, 0, 0.55)",
    lineWidth: 1,
    lineStyle: LC.LineStyle.Dotted,
    priceLineVisible: false,
    lastValueVisible: false,
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

  const markersPlugin = priceSeries;

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

    priceSeries.setData(priceData);
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
    markersPlugin.setMarkers(markers);

    if (warmEnd != null && priceData.length) {
      // Warm-up visual: slightly dim via markers note only (LWC has no segment style)
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
