/* Graphiques de la page vitrine.

   Mêmes règles que dashboard/static/js/charts.js : aucune couleur écrite en dur,
   tout est lu dans les variables CSS au moment du rendu. Les données, elles, sont
   des chiffres de démonstration figés — la vitrine illustre le produit, elle ne
   restitue pas une analyse réelle. */
(function () {
  "use strict";

  var root = document.body;

  function read(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
  }

  function tokens() {
    return {
      accent: read("--color-accent"),
      ink700: read("--color-ink-700"),
      ink500: read("--color-ink-500"),
      line: read("--color-line"),
      line300: read("--color-line-300"),
      surface: read("--color-surface"),
      surface50: read("--color-surface-50"),
      surface200: read("--color-surface-200"),
      surface300: read("--color-surface-300"),
      info: read("--color-info-ink"),
      llm: read("--color-llm-ink"),
      a: read("--color-score-a"),
      b: read("--color-score-b"),
      c: read("--color-score-c"),
      d: read("--color-score-d"),
      e: read("--color-score-e"),
    };
  }

  var FONT = "Inter, system-ui, sans-serif";
  var MONO = '"IBM Plex Mono", monospace';

  /* `recompute` est réservé aux graphiques dont les données dépendent de la
     taille de la boîte : redessiner ne suffit pas, il faut refaire l'option. */
  function mount(id, build, recompute) {
    var el = document.getElementById(id);
    if (!el) return null;
    var chart = window.echarts.init(el, null, { renderer: "svg" });
    chart.setOption(build(el));
    new ResizeObserver(function () {
      // `setOption` seul ne redimensionne pas l'instance : sans `resize` le SVG
      // garde sa largeur d'origine et déborde de la carte.
      chart.resize();
      if (recompute) chart.setOption(build(el), true);
    }).observe(el);
    return chart;
  }

  /* La disposition en force n'est pas bornée et déborderait d'une carte de
     320px : les nœuds sont posés à la main (layout 'none') en coordonnées
     relatives, puis projetés sur la taille réelle de la boîte. */
  function graphOption(el) {
    var t = tokens();
    var w = el.clientWidth || 800;
    var h = el.clientHeight || 320;
    var padL = 78;
    var padR = 92;
    var padT = 26;
    var padB = 62;
    var X = function (nx) {
      return padL + nx * Math.max(120, w - padL - padR);
    };
    var Y = function (ny) {
      return padT + ny * Math.max(90, h - padT - padB);
    };
    var nodes = [
      { id: "Procédures RH", size: 46, cat: 0, nx: 0.12, ny: 0.36, pos: "top" },
      { id: "Congés", size: 34, cat: 0, nx: 0.04, ny: 0.78, pos: "bottom" },
      { id: "Télétravail", size: 28, cat: 0, nx: 0.28, ny: 0.95, pos: "right" },
      { id: "Accès & habilitations", size: 26, cat: 2, nx: 0.2, ny: 0.04, pos: "top" },
      { id: "Onboarding", size: 22, cat: 1, nx: 0.34, ny: 0.52, pos: "right" },
      { id: "Sécurité SI", size: 38, cat: 2, nx: 0.54, ny: 0.12, pos: "right" },
      { id: "Support N1", size: 30, cat: 1, nx: 0.52, ny: 0.78, pos: "bottom" },
      { id: "Support N2", size: 24, cat: 1, nx: 0.7, ny: 0.48, pos: "right" },
      { id: "Facturation", size: 20, cat: 3, nx: 0.92, ny: 0.8, pos: "left" },
      { id: "Achats", size: 16, cat: 3, nx: 1, ny: 0.34, pos: "left" },
    ];
    var links = [
      ["Procédures RH", "Congés"],
      ["Procédures RH", "Télétravail"],
      ["Congés", "Télétravail"],
      ["Procédures RH", "Onboarding"],
      ["Onboarding", "Support N1"],
      ["Support N1", "Support N2"],
      ["Sécurité SI", "Accès & habilitations"],
      ["Accès & habilitations", "Onboarding"],
      ["Facturation", "Achats"],
      ["Facturation", "Support N2"],
      ["Sécurité SI", "Support N2"],
    ];
    return {
      textStyle: { fontFamily: FONT },
      tooltip: {
        formatter: function (p) {
          return p.dataType === "node" ? p.name + " — " + p.value + " documents" : "";
        },
      },
      legend: [
        {
          data: ["RH", "Support", "Sécurité", "Finance"],
          bottom: 0,
          textStyle: { color: t.ink500, fontSize: 11 },
          icon: "circle",
          itemWidth: 8,
          itemHeight: 8,
        },
      ],
      color: [t.accent, t.info, t.d, t.llm],
      series: [
        {
          type: "graph",
          layout: "none",
          categories: [
            { name: "RH" },
            { name: "Support" },
            { name: "Sécurité" },
            { name: "Finance" },
          ],
          data: nodes.map(function (n) {
            return {
              name: n.id,
              value: n.size,
              category: n.cat,
              x: X(n.nx),
              y: Y(n.ny),
              symbolSize: 12 + n.size * 0.58,
              label: { position: n.pos },
            };
          }),
          links: links.map(function (l) {
            return { source: l[0], target: l[1] };
          }),
          lineStyle: { color: t.line300, width: 1.2, curveness: 0.08 },
          label: { show: true, position: "right", color: t.ink700, fontSize: 11, fontFamily: FONT },
          labelLayout: { hideOverlap: true },
          emphasis: { focus: "adjacency", lineStyle: { width: 2.4, color: t.accent } },
        },
      ],
    };
  }

  function build() {
    var t = tokens();

    mount("sc-v-radar", function () {
      return {
        textStyle: { fontFamily: FONT },
        tooltip: { trigger: "item" },
        radar: {
          indicator: [
            { name: "Redondance", max: 100 },
            { name: "Cohérence", max: 100 },
            { name: "Couverture", max: 100 },
            { name: "Structure", max: 100 },
            { name: "Retrouvabilité", max: 100 },
            { name: "Gouvernance", max: 100 },
          ],
          radius: "68%",
          center: ["50%", "52%"],
          axisName: { color: t.ink500, fontSize: 11, fontFamily: FONT },
          splitLine: { lineStyle: { color: t.surface300 } },
          splitArea: { areaStyle: { color: [t.surface, t.surface50] } },
          axisLine: { lineStyle: { color: t.surface300 } },
        },
        series: [
          {
            type: "radar",
            symbolSize: 4,
            data: [
              {
                value: [92, 88, 84, 90, 86, 80],
                name: "Cible",
                lineStyle: { color: t.line300, type: "dashed", width: 1.5 },
                itemStyle: { color: t.line300 },
                areaStyle: { color: alpha(t.line300, 0.12) },
              },
              {
                value: [48, 55, 72, 81, 64, 76],
                name: "Base support N2",
                lineStyle: { color: t.accent, width: 2 },
                itemStyle: { color: t.accent },
                areaStyle: { color: alpha(t.accent, 0.16) },
              },
            ],
          },
        ],
      };
    });

    var drops = [
      { name: "Redondance", v: 10.4, c: t.e },
      { name: "Cohérence", v: 11.3, c: t.d },
      { name: "Couverture", v: 5.6, c: t.c },
      { name: "Structure", v: 2.9, c: t.b },
      { name: "Retrouvabilité", v: 4.3, c: t.b },
      { name: "Gouvernance", v: 1.9, c: t.a },
    ];
    // Cascade : une série transparente porte les barres visibles à la hauteur
    // du cumul restant.
    var run = 100;
    var base = [];
    var visible = [];
    drops.forEach(function (d) {
      run -= d.v;
      base.push(run);
      visible.push({ value: d.v, itemStyle: { color: d.c, borderRadius: [3, 3, 0, 0] } });
    });

    mount("sc-v-waterfall", function () {
      return {
        textStyle: { fontFamily: FONT },
        grid: { left: 6, right: 10, top: 22, bottom: 4, containLabel: true },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: function (p) {
            var bar = p.find(function (x) {
              return x.seriesName === "perte";
            });
            return bar ? bar.name + " — " + bar.value + " pts perdus" : "";
          },
        },
        xAxis: {
          type: "category",
          data: drops.map(function (d) {
            return d.name;
          }),
          axisLabel: { color: t.ink500, fontSize: 10.5, interval: 0, rotate: 22 },
          axisLine: { lineStyle: { color: t.line } },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          min: 60,
          max: 100,
          splitLine: { lineStyle: { color: t.surface200 } },
          axisLabel: { color: t.ink500, fontSize: 10.5, fontFamily: MONO },
        },
        series: [
          {
            name: "socle",
            type: "bar",
            stack: "w",
            itemStyle: { color: "transparent" },
            emphasis: { itemStyle: { color: "transparent" } },
            data: base,
          },
          {
            name: "perte",
            type: "bar",
            stack: "w",
            barWidth: "46%",
            data: visible,
            label: {
              show: true,
              position: "top",
              formatter: "−{c}",
              color: t.ink500,
              fontSize: 10.5,
              fontFamily: MONO,
            },
          },
        ],
      };
    });

    mount("sc-v-graph", graphOption, true);

    var cov = [
      { n: "RH", v: 91 },
      { n: "Support", v: 78 },
      { n: "Sécurité", v: 64 },
      { n: "Finance", v: 52 },
      { n: "Juridique", v: 38 },
      { n: "Achats", v: 21 },
    ];
    var covColor = function (v) {
      if (v >= 85) return t.a;
      if (v >= 70) return t.b;
      if (v >= 55) return t.c;
      if (v >= 40) return t.d;
      return t.e;
    };

    mount("sc-v-coverage", function () {
      return {
        textStyle: { fontFamily: FONT },
        grid: { left: 4, right: 34, top: 8, bottom: 4, containLabel: true },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          valueFormatter: function (v) {
            return v + " %";
          },
        },
        xAxis: {
          type: "value",
          max: 100,
          splitLine: { lineStyle: { color: t.surface200 } },
          axisLabel: { color: t.ink500, fontSize: 10.5, formatter: "{value} %", fontFamily: MONO },
        },
        yAxis: {
          type: "category",
          data: cov
            .map(function (c) {
              return c.n;
            })
            .reverse(),
          axisLabel: { color: t.ink700, fontSize: 11.5 },
          axisLine: { lineStyle: { color: t.line } },
          axisTick: { show: false },
        },
        series: [
          {
            type: "bar",
            barWidth: "54%",
            data: cov
              .map(function (c) {
                return { value: c.v, itemStyle: { color: covColor(c.v), borderRadius: [0, 4, 4, 0] } };
              })
              .reverse(),
            label: {
              show: true,
              position: "right",
              formatter: "{c} %",
              color: t.ink500,
              fontSize: 10.5,
              fontFamily: MONO,
            },
          },
        ],
      };
    });

    mount("sc-v-recall", function () {
      return {
        textStyle: { fontFamily: FONT },
        grid: { left: 4, right: 12, top: 30, bottom: 4, containLabel: true },
        tooltip: { trigger: "axis" },
        legend: {
          data: ["avant", "après"],
          top: 0,
          right: 0,
          textStyle: { color: t.ink500, fontSize: 11 },
          icon: "roundRect",
          itemWidth: 12,
          itemHeight: 3,
        },
        xAxis: {
          type: "category",
          boundaryGap: false,
          data: ["k=1", "k=3", "k=5", "k=10", "k=20"],
          axisLabel: { color: t.ink500, fontSize: 10.5, fontFamily: MONO },
          axisLine: { lineStyle: { color: t.line } },
          axisTick: { show: false },
        },
        yAxis: {
          type: "value",
          max: 1,
          splitLine: { lineStyle: { color: t.surface200 } },
          axisLabel: { color: t.ink500, fontSize: 10.5, fontFamily: MONO },
        },
        series: [
          {
            name: "avant",
            type: "line",
            smooth: true,
            symbolSize: 5,
            data: [0.31, 0.48, 0.57, 0.66, 0.74],
            lineStyle: { color: t.line300, width: 2 },
            itemStyle: { color: t.line300 },
          },
          {
            name: "après",
            type: "line",
            smooth: true,
            symbolSize: 5,
            data: [0.44, 0.63, 0.74, 0.85, 0.92],
            lineStyle: { color: t.accent, width: 2.4 },
            itemStyle: { color: t.accent },
            areaStyle: { color: alpha(t.accent, 0.1) },
          },
        ],
      };
    });
  }

  /* ECharts peint dans un canvas SVG et ne résout ni color-mix() ni var(). */
  function alpha(color, a) {
    var hex = String(color || "").trim();
    if (hex[0] !== "#") return hex;
    if (hex.length === 4) {
      hex = "#" + hex[1] + hex[1] + hex[2] + hex[2] + hex[3] + hex[3];
    }
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  function start() {
    if (!window.echarts) return void setTimeout(start, 60);
    build();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
