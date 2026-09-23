import {
  fetchBurn,
  fetchRecords,
  fetchSeries,
  fetchTicker,
  fetchTrades,
} from "./api.js";
import { createMeiaChart } from "./chart.js";

const TIP_EQ = "equivalente em tip a R$1 = 1 min";

/** Glossário: o que cada rótulo das seções significa. */
const GLOSSARY = {
  "Parede de queima":
    "Quanto em tip-equivalente (R$/h) é preciso comprar só para o timer ficar parado. O relógio queima 60 min por hora → R$60/h no peg.",
  "Ritmo atual":
    "Média de minutos comprados por hora desde a gênese, convertida em R$/h tip-equivalente.",
  Cobertura:
    "Porcentagem da queima do relógio que as compras cobriram (comprado ÷ queimado).",
  Déficit:
    "Diferença entre a parede (R$60/h) e o ritmo atual. Positivo = o timer ainda está encolhendo em média.",
  Comprado:
    "Soma de todos os minutos adicionados ao timer (grants) desde a gênese deste app.",
  Queimado:
    "Minutos que o relógio consumiu sozinho desde a gênese (tempo decorrido).",
  "ETA da pista":
    "Estimativa de quando o timer zera se o déficit atual continuar (pista / runway).",
  "Melhor hora":
    "Hora UTC com mais minutos comprados desde a gênese.",
  "Pior hora":
    "Hora UTC com menos minutos comprados (geralmente zero = só o relógio vendendo).",
  "Maior líquido na hora":
    "Maior saldo numa hora: minutos comprados menos os 60 min que o relógio queima.",
  "Mais tempo acima do equilíbrio":
    "Maior sequência contínua de horas com m > 0 (compras cobrindo mais que a queima).",
  "Maior estiagem":
    "Maior sequência contínua de horas sem nenhuma compra.",
  "Mix de tamanhos":
    "Distribuição dos tamanhos dos grants (ex.: 1800s = sub Kick). Classes são “prováveis” — o feed não diz a origem com certeza.",
};

const el = (tag, props = {}, kids = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "className") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "attrs") {
      for (const [ak, av] of Object.entries(v)) node.setAttribute(ak, av);
    } else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v != null) node[k] = v;
  }
  for (const c of kids) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
};

/** Rótulo com popup de hover explicando o termo. */
function termLabel(text, tip) {
  const explain = tip || GLOSSARY[text] || "";
  return el("span", {
    className: "term",
    text,
    attrs: {
      tabindex: "0",
      "data-tip": explain,
    },
  });
}

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function fmtNum(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${String(h).padStart(2, "0")}h`;
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

let currentTf = "5m";
let chartApi = null;
let modelConstants = null;

function renderTicker(data) {
  const price = document.getElementById("price");
  const chg1h = document.getElementById("chg1h");
  const chg24h = document.getElementById("chg24h");
  const pulse = document.getElementById("pulse");

  price.textContent = fmtNum(data.price, 2);
  chg1h.textContent = `1h ${fmtPct(data.change_1h_pct)}`;
  chg1h.className = `chg mono ${data.change_1h_pct >= 0 ? "up" : "down"}`;
  chg1h.title = "Variação do preço na última hora";
  chg24h.textContent = `24h ${fmtPct(data.change_24h_pct)}`;
  chg24h.className = `chg mono ${data.change_24h_pct >= 0 ? "up" : "down"}`;
  chg24h.title = "Variação do preço nas últimas 24 horas";

  pulse.className = "pulse";
  if (data.degraded) {
    pulse.classList.add("degraded");
    pulse.title = "Feed degradado: sem atualização recente";
  } else if (data.stale) {
    pulse.classList.add("stale");
    pulse.title = "Feed atrasado: mostrando último valor conhecido";
  } else {
    pulse.title = "Feed ao vivo";
  }

  document.getElementById("high24").textContent = fmtNum(data.high_24h, 2);
  document.getElementById("low24").textContent = fmtNum(data.low_24h, 2);
  document.getElementById("ath").textContent = fmtNum(data.ath, 2);
  document.getElementById("dd").textContent = fmtPct(data.drawdown_pct);
  document.getElementById("mcap").textContent = fmtDuration(data.market_cap_seconds);
  document.getElementById("lm").textContent = `L=${fmtNum(data.L, 4)} · m=${fmtNum(data.m, 2)}`;

  modelConstants = data.model;
  updateFormulaTooltip();
}

function updateFormulaTooltip() {
  const box = document.getElementById("formulaTip");
  const m = modelConstants || { alpha: 1.0, kappa: 0.05, window_minutes: 60, anchor: "peak" };
  clear(box);
  box.appendChild(
    el("div", {}, [
      el("code", {
        text: "P = 100 · L^α · e^(κ·m)",
      }),
      el("p", {
        text: `L = R/R_ref (fração da vida de pico restante) · m = (minutos comprados nos últimos ${m.window_minutes} min)/${m.window_minutes} − 1`,
      }),
      el("p", {
        text: `α = ${m.alpha} · κ = ${m.kappa} · W = ${m.window_minutes} min · âncora = ${m.anchor === "peak" ? "pico" : m.anchor}`,
      }),
      el("p", {
        text: "Neutro 100 = vida no pico + compras equilibrando a queima. Linha pontilhada = sangria pura (ninguém compra).",
      }),
    ])
  );
}

function renderPremarket(ticker, burn, trades, series) {
  const box = document.getElementById("premarket");
  const bars = (series && series.bars) || [];
  const littleData = bars.length < 5;
  if (!littleData) {
    box.classList.remove("show");
    return;
  }
  box.classList.add("show");
  clear(box);
  const since = ticker.genesis_at ? fmtTime(ticker.genesis_at) : "a gênese";
  box.appendChild(el("h2", { text: "Pré-mercado" }));
  box.appendChild(el("p", { text: `Coletando dados desde ${since}` }));
  box.appendChild(
    el("p", {
      text: `Trades desde o início: ${(trades.trades || []).length}`,
    })
  );
  box.appendChild(
    el("p", {
      text: `Minutos comprados ${fmtNum(burn.minutes_bought_total, 1)} vs queimados ${fmtNum(burn.minutes_burned_total, 1)} · cobertura ${fmtNum(burn.coverage_pct, 1)}%`,
    })
  );
  box.appendChild(
    el("p", {
      className: "mono",
      text: burn.label || TIP_EQ,
    })
  );
}

function renderBurn(data) {
  const root = document.getElementById("burnBody");
  clear(root);
  const rows = [
    ["Parede de queima", `R$ ${fmtNum(data.burn_wall_brl_per_hour, 0)}/h`],
    ["Ritmo atual", `R$ ${fmtNum(data.pace_brl_per_hour, 1)}/h`],
    ["Cobertura", `${fmtNum(data.coverage_pct, 1)}%`],
    ["Déficit", `R$ ${fmtNum(data.deficit_brl_per_hour, 1)}/h`],
    ["Comprado", `${fmtNum(data.minutes_bought_total, 1)} min`],
    ["Queimado", `${fmtNum(data.minutes_burned_total, 1)} min`],
    ["ETA da pista", data.runway_eta ? fmtTime(data.runway_eta) : "—"],
  ];
  const grid = el("div", { className: "burn-grid" });
  for (const [label, value] of rows) {
    grid.appendChild(
      el("div", { className: "burn-row" }, [
        termLabel(label),
        el("strong", { className: "mono", text: value }),
      ])
    );
  }
  const pct = Math.max(0, Math.min(100, data.coverage_pct || 0));
  const meter = el("div", { className: "meter" }, [el("i")]);
  meter.firstChild.style.width = `${pct}%`;
  root.appendChild(grid);
  root.appendChild(meter);
  root.appendChild(
    el("p", {
      className: "hint mono term",
      text: data.label || TIP_EQ,
      attrs: {
        tabindex: "0",
        "data-tip":
          "Todo valor em R$ nesta tela é equivalente em tip: R$1 doado como tip = +1 minuto no timer. Subs e bits dão tempo a outras taxas.",
      },
    })
  );
}

function renderTape(data) {
  const root = document.getElementById("tapeBody");
  clear(root);
  const list = el("ul", { className: "tape" });
  const trades = data.trades || [];
  if (!trades.length) {
    list.appendChild(el("li", { text: "Nenhum trade desde a gênese ainda." }));
  }
  for (const t of trades) {
    const label = t.likely_label || "trade provável";
    list.appendChild(
      el("li", {}, [
        el("strong", {
          className: "term",
          text: label,
          attrs: {
            tabindex: "0",
            "data-tip":
              "Classificação “provável” pelo tamanho do grant em segundos (regras do timer). O feed não informa a origem real nem o nome do doador.",
          },
        }),
        el("span", { className: "mono", text: `R$ ${fmtNum(t.tip_equivalent_brl, 1)}` }),
        el("span", {
          className: "meta mono",
          text: `${fmtTime(t.at)} · ±${t.precision_seconds || "?"}s · ${t.tip_equivalent_label || data.label || TIP_EQ}`,
        }),
      ])
    );
  }
  root.appendChild(list);
}

function renderRecords(data) {
  const root = document.getElementById("recordsBody");
  clear(root);
  const list = el("ul", { className: "records" });
  const items = [
    [
      "Melhor hora",
      data.best_hour
        ? `${fmtTime(data.best_hour.at)} · ${fmtNum(data.best_hour.bought_min, 1)} min · m=${fmtNum(data.best_hour.m, 2)}`
        : "—",
    ],
    [
      "Pior hora",
      data.worst_hour
        ? `${fmtTime(data.worst_hour.at)} · ${fmtNum(data.worst_hour.bought_min, 1)} min`
        : "—",
    ],
    ["Maior líquido na hora", `${fmtNum(data.biggest_hour_net_min, 1)} min`],
    ["Mais tempo acima do equilíbrio", `${data.longest_above_breakeven_hours || 0} h`],
    ["Maior estiagem", `${data.longest_dry_spell_hours || 0} h`],
  ];
  for (const [k, v] of items) {
    list.appendChild(
      el("li", {}, [
        termLabel(k),
        el("strong", { className: "mono", text: v }),
      ])
    );
  }
  if (data.size_distribution && data.size_distribution.length) {
    const dist = data.size_distribution
      .slice(0, 6)
      .map((s) => `${s.seconds}s×${s.count}`)
      .join(" · ");
    list.appendChild(
      el("li", {}, [
        termLabel("Mix de tamanhos"),
        el("strong", { className: "mono", text: dist }),
        el("span", {
          className: "meta",
          text: "só classes prováveis — não existem nomes de doadores nos dados",
        }),
      ])
    );
  }
  root.appendChild(list);
}

async function refresh() {
  try {
    const [ticker, series, trades, burn, records] = await Promise.all([
      fetchTicker(),
      fetchSeries(currentTf),
      fetchTrades(),
      fetchBurn(),
      fetchRecords(),
    ]);
    renderTicker(ticker);
    if (chartApi) chartApi.render(series);
    renderPremarket(ticker, burn, trades, series);
    renderBurn(burn);
    renderTape(trades);
    renderRecords(records);
  } catch (err) {
    console.error(err);
    const pulse = document.getElementById("pulse");
    pulse.className = "pulse degraded";
    pulse.title = "Erro ao carregar dados";
  }
}

function wireUi() {
  document.getElementById("formulaBtn").addEventListener("click", () => {
    document.getElementById("formulaTip").classList.toggle("open");
  });

  const bar = document.getElementById("tfBar");
  bar.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-tf]");
    if (!btn) return;
    currentTf = btn.getAttribute("data-tf");
    for (const b of bar.querySelectorAll("button")) b.classList.toggle("active", b === btn);
    refresh();
  });
}

function main() {
  wireUi();
  const container = document.getElementById("chart");
  chartApi = createMeiaChart(container);
  refresh();
  setInterval(refresh, 10000);
}

main();
