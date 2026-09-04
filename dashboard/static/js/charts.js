/* Thème des graphiques SCORE.
   Les couleurs sont lues dans les variables CSS au moment du rendu, jamais écrites
   en dur : c'est ce qui permet à l'accent configurable et au thème sombre de
   s'appliquer aussi aux graphiques. */
(function () {
  "use strict";

  function read(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  var scChart = {
    /* Lu à chaque appel : le thème peut avoir changé depuis le dernier rendu. */
    tokens: function () {
      return {
        accent: read("--accent"),
        ink: read("--color-ink"),
        ink700: read("--color-ink-700"),
        ink500: read("--color-ink-500"),
        ink400: read("--color-ink-400"),
        line: read("--color-line"),
        line300: read("--color-line-300"),
        line400: read("--color-line-400"),
        surface: read("--color-surface"),
        surface50: read("--color-surface-50"),
        surface100: read("--color-surface-100"),
        surface200: read("--color-surface-200"),
        a: read("--color-score-a"),
        b: read("--color-score-b"),
        c: read("--color-score-c"),
        d: read("--color-score-d"),
        e: read("--color-score-e"),
      };
    },

    /* Barème colorFor de la maquette — volontairement différent des seuils de
       note gradeOf (80/65/50/35). Ne pas fusionner les deux. */
    nutri: function (score) {
      var t = this.tokens();
      if (score >= 70) return t.a;
      if (score >= 55) return t.b;
      if (score >= 40) return t.c;
      if (score >= 25) return t.d;
      return t.e;
    },

    /* Palette des clusters : teintes distinctes, l'accent en tête. */
    palette: function () {
      var t = this.tokens();
      return [t.accent, "#3e7cb1", "#8b5cf6", t.d, "#0aa06e", "#f472b6", t.b, "#a78bfa"];
    },

    /* Transparence sur une couleur de token.
       ECharts peint dans un canvas, qui ne comprend ni color-mix() ni var() :
       il faut lui passer une valeur rgba() déjà résolue. */
    alpha: function (color, a) {
      var hex = String(color || "").trim();
      if (hex[0] !== "#") return hex;
      if (hex.length === 4) {
        hex = "#" + hex[1] + hex[1] + hex[2] + hex[2] + hex[3] + hex[3];
      }
      var n = parseInt(hex.slice(1), 16);
      return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
    },

    accentAlpha: function (a) {
      return this.alpha(read("--accent"), a);
    },

    /* Base commune à toutes les instances ECharts. */
    base: function () {
      var t = this.tokens();
      return {
        backgroundColor: "transparent",
        textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
        tooltip: {
          backgroundColor: t.surface,
          borderColor: t.line,
          borderWidth: 1,
          padding: [7, 10],
          textStyle: { color: t.ink, fontSize: 11.5 },
          extraCssText:
            "max-width:320px;white-space:normal;word-break:break-word;" +
            "box-shadow:0 6px 20px rgba(20,24,26,.12);border-radius:8px;",
        },
      };
    },

    /* Axe de valeurs : ligne masquée, grille en pointillé discret. */
    valueAxis: function (extra) {
      var t = this.tokens();
      return Object.assign(
        {
          type: "value",
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: t.ink500, fontSize: 10 },
          splitLine: { lineStyle: { color: t.surface200, type: "dashed" } },
        },
        extra || {},
      );
    },

    /* Axe de catégories : ligne visible, pas de grille. */
    categoryAxis: function (extra) {
      var t = this.tokens();
      return Object.assign(
        {
          type: "category",
          axisLine: { lineStyle: { color: t.line } },
          axisTick: { show: false },
          axisLabel: { color: t.ink700, fontSize: 11 },
          splitLine: { show: false },
        },
        extra || {},
      );
    },

    /* Rejoue le rendu quand le thème bascule, pour que les couleurs suivent. */
    onThemeChange: function (callback) {
      new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
          if (records[i].attributeName === "data-theme") {
            callback();
            return;
          }
        }
      }).observe(document.documentElement, { attributes: true });
    },

    /* Redessine sur redimensionnement du conteneur, pas de la fenêtre : les
       graphiques vivent dans des grilles qui changent sans que la fenêtre bouge. */
    autoResize: function (el, chart) {
      new ResizeObserver(function () {
        chart.resize();
      }).observe(el);
    },

    /* Lit un bloc json_script. Les vues sérialisent déjà en JSON, donc
       json_script encode une chaîne : d'où le double parse. */
    readJson: function (id) {
      var el = document.getElementById(id);
      if (!el) return null;
      try {
        return JSON.parse(JSON.parse(el.textContent));
      } catch (e) {
        return null;
      }
    },

    /* Répartitions compactes des cartes KPI. Vivent ici plutôt que dans un
       gabarit : les cartes sont rendues dans une frame chargée à part, et le
       code d'initialisation doit rester disponible des deux côtés. */
    compactPie: function (containerId, dataId, colorsFor) {
      var self = this;
      drawCompact(this, containerId, dataId, function (data, t) {
        var map = colorsFor ? colorsFor(t) : null;
        return {
          series: [
            {
              type: "pie",
              radius: ["34%", "72%"],
              center: ["50%", "50%"],
              padAngle: 2,
              itemStyle: { borderRadius: 3 },
              label: { show: false },
              data: data.map(function (d, i) {
                return { name: d.name, value: d.value, itemStyle: { color: seriesColor(self, d.name, i, map) } };
              }),
              animationDuration: 600,
            },
          ],
        };
      });
    },

    compactBar: function (containerId, dataId, colorsFor) {
      var self = this;
      drawCompact(this, containerId, dataId, function (data, t) {
        var map = colorsFor ? colorsFor(t) : null;
        return {
          grid: { top: 18, bottom: 2, left: 2, right: 2, containLabel: true },
          xAxis: {
            type: "category",
            data: data.map(function (d) {
              return d.name;
            }),
            show: false,
          },
          yAxis: { type: "value", show: false },
          series: [
            {
              type: "bar",
              barMaxWidth: 18,
              label: { show: true, position: "top", fontSize: 10, color: t.ink500, formatter: "{c}" },
              data: data.map(function (d, i) {
                return {
                  name: d.name,
                  value: d.value,
                  itemStyle: { color: seriesColor(self, d.name, i, map), borderRadius: [3, 3, 0, 0] },
                };
              }),
              animationDuration: 600,
            },
          ],
        };
      });
    },
  };

  /* Sans table dédiée, la teinte n'encode qu'une identité de série. */
  function seriesColor(api, name, index, map) {
    if (map && map[name]) return map[name];
    var palette = api.palette();
    return palette[index % palette.length];
  }

  function drawCompact(api, containerId, dataId, optionsFor) {
    var el = document.getElementById(containerId);
    var data = api.readJson(dataId);
    if (!el || !data || !data.length || !window.echarts) return;
    var chart = echarts.init(el, null, { renderer: "canvas" });

    function draw() {
      var base = api.base();
      base.tooltip.trigger = "item";
      base.tooltip.appendToBody = true;
      chart.setOption(Object.assign(base, optionsFor(data, api.tokens())));
    }

    draw();
    api.autoResize(el, chart);
    api.onThemeChange(draw);
  }

  window.scChart = scChart;
})();
