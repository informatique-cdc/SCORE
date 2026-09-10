/* Comportements d'interface SCORE.
   Remplace le bundle Bootstrap : modales, panneaux latéraux, menus déroulants,
   sous-menus, onglets, sections dépliables, infobulles et dialogue de confirmation.
   Tout passe par de la délégation sur `document` afin de survivre aux échanges
   de Turbo Frames sans réinitialisation. */
(function () {
  "use strict";

  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  /* ─────────────────────────── Superpositions ─────────────────────────── */

  var openOverlays = [];

  function overlayByName(name) {
    return document.querySelector('[data-overlay="' + CSS.escape(name) + '"]');
  }

  function lockScroll() {
    document.body.style.overflow = "hidden";
  }

  function unlockScroll() {
    if (!openOverlays.length) document.body.style.overflow = "";
  }

  function openOverlay(name) {
    var scrim = overlayByName(name);
    if (!scrim || openOverlays.indexOf(scrim) !== -1) return null;

    closeAllDropdowns();

    scrim.hidden = false;
    scrim.dataset.overlayReturn = "";
    var active = document.activeElement;
    if (active && active !== document.body) {
      active.setAttribute("data-overlay-restore", "");
    }

    var dialog = scrim.querySelector("[data-overlay-dialog]");
    if (dialog) {
      dialog.hidden = false;
      // Le panneau latéral glisse : il lui faut une frame entre l'affichage et la transition.
      requestAnimationFrame(function () {
        dialog.classList.add("is-open");
      });
    }

    openOverlays.push(scrim);
    lockScroll();

    var target =
      scrim.querySelector("[autofocus]") ||
      (dialog || scrim).querySelector(FOCUSABLE);
    if (target) target.focus();

    scrim.dispatchEvent(new CustomEvent("overlay:open", { bubbles: true }));
    return scrim;
  }

  function closeOverlay(scrim) {
    var idx = openOverlays.indexOf(scrim);
    if (idx === -1) return;
    openOverlays.splice(idx, 1);

    var dialog = scrim.querySelector("[data-overlay-dialog]");
    if (dialog) dialog.classList.remove("is-open");

    scrim.hidden = true;
    unlockScroll();

    var restore = document.querySelector("[data-overlay-restore]");
    if (restore) {
      restore.removeAttribute("data-overlay-restore");
      if (document.contains(restore)) restore.focus();
    }

    scrim.dispatchEvent(new CustomEvent("overlay:close", { bubbles: true }));
  }

  function closeTopOverlay() {
    if (openOverlays.length) closeOverlay(openOverlays[openOverlays.length - 1]);
  }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-overlay-open]");
    if (opener) {
      e.preventDefault();
      openOverlay(opener.getAttribute("data-overlay-open"));
      return;
    }

    var closer = e.target.closest("[data-overlay-close]");
    if (closer) {
      e.preventDefault();
      var owner = closer.closest("[data-overlay]");
      if (owner) closeOverlay(owner);
      return;
    }

    // Clic sur le fond, en dehors du dialogue.
    var scrim = e.target.closest("[data-overlay]");
    if (scrim && e.target === scrim) closeOverlay(scrim);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && openOverlays.length) {
      e.preventDefault();
      closeTopOverlay();
      return;
    }

    if (e.key !== "Tab" || !openOverlays.length) return;

    // Piège le focus dans le dialogue le plus haut de la pile.
    var scrim = openOverlays[openOverlays.length - 1];
    var scope = scrim.querySelector("[data-overlay-dialog]") || scrim;
    var items = Array.prototype.filter.call(
      scope.querySelectorAll(FOCUSABLE),
      function (el) {
        return el.offsetParent !== null;
      },
    );
    if (!items.length) return;

    var first = items[0];
    var last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });

  /* ─────────────────────────── Menus déroulants ─────────────────────────── */

  function closeAllDropdowns(except) {
    document.querySelectorAll("[data-dropdown].is-open").forEach(function (el) {
      if (el !== except) el.classList.remove("is-open");
    });
  }

  document.addEventListener("click", function (e) {
    var toggle = e.target.closest("[data-dropdown-toggle]");
    if (toggle) {
      e.preventDefault();
      var dd = toggle.closest("[data-dropdown]");
      closeAllDropdowns(dd);
      dd.classList.toggle("is-open");
      return;
    }
    if (!e.target.closest("[data-dropdown]")) closeAllDropdowns();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !openOverlays.length) closeAllDropdowns();
  });

  /* ─────────────────────────── Sous-menu du rail ─────────────────────────── */

  document.addEventListener("click", function (e) {
    var toggle = e.target.closest("[data-submenu-toggle]");
    if (!toggle) return;
    var submenu = toggle.closest("[data-submenu]");
    var items = submenu && submenu.querySelector("[data-submenu-items]");
    // Sans enfant, le lien doit naviguer normalement.
    if (!items || !items.children.length) return;
    e.preventDefault();
    submenu.classList.toggle("is-open");
  });

  /* ─────────────────────────── Onglets ─────────────────────────── */

  document.addEventListener("click", function (e) {
    var tab = e.target.closest("[data-tab]");
    if (!tab) return;
    var group = tab.closest("[data-tabs]");
    if (!group) return;
    e.preventDefault();

    var key = tab.getAttribute("data-tab");
    group.querySelectorAll("[data-tab]").forEach(function (el) {
      el.classList.toggle("is-active", el === tab);
      el.setAttribute("aria-selected", el === tab ? "true" : "false");
    });
    group.querySelectorAll("[data-tab-panel]").forEach(function (el) {
      el.hidden = el.getAttribute("data-tab-panel") !== key;
    });
  });

  /* ─────────────────────────── Sections dépliables ─────────────────────────── */

  document.addEventListener("click", function (e) {
    var toggle = e.target.closest("[data-disclosure]");
    if (!toggle) return;
    e.preventDefault();

    var name = toggle.getAttribute("data-disclosure");
    var panel = document.querySelector(
      '[data-disclosure-panel="' + CSS.escape(name) + '"]',
    );
    if (!panel) return;

    var willOpen = panel.hidden;
    var group = toggle.getAttribute("data-disclosure-group");

    // En accordéon, l'ouverture d'une section referme ses sœurs.
    if (group && willOpen) {
      document
        .querySelectorAll('[data-disclosure-group="' + CSS.escape(group) + '"]')
        .forEach(function (sib) {
          if (sib === toggle) return;
          var sibPanel = document.querySelector(
            '[data-disclosure-panel="' +
              CSS.escape(sib.getAttribute("data-disclosure")) +
              '"]',
          );
          if (sibPanel) sibPanel.hidden = true;
          sib.classList.remove("is-open");
          sib.setAttribute("aria-expanded", "false");
        });
    }

    panel.hidden = !willOpen;
    toggle.classList.toggle("is-open", willOpen);
    toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
  });

  /* ─────────────────────────── Infobulles ─────────────────────────── */

  var tip = null;

  function hideTip() {
    if (tip) {
      tip.remove();
      tip = null;
    }
  }

  document.addEventListener("mouseover", function (e) {
    var host = e.target.closest("[data-tip]");
    if (!host) return;
    hideTip();
    tip = document.createElement("div");
    tip.className = "tooltip-viz";
    tip.textContent = host.getAttribute("data-tip");
    document.body.appendChild(tip);

    var r = host.getBoundingClientRect();
    tip.style.left = Math.round(r.left + r.width / 2 - tip.offsetWidth / 2) + "px";
    tip.style.top = Math.round(r.top + window.scrollY - tip.offsetHeight - 8) + "px";
  });

  document.addEventListener("mouseout", function (e) {
    if (e.target.closest("[data-tip]")) hideTip();
  });

  document.addEventListener("scroll", hideTip, true);

  /* ─────────────────────────── Contraste du rail ─────────────────────────── */

  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-rail-toggle]")) return;
    var rail = document.querySelector("[data-rail]");
    if (!rail) return;
    var next = rail.getAttribute("data-rail") === "dark" ? "light" : "dark";
    rail.setAttribute("data-rail", next);
    try {
      localStorage.setItem("sc-rail", next);
    } catch (err) {
      /* stockage indisponible : la préférence ne survit pas à la session */
    }
  });

  /* ─────────────────────────── Thème clair / sombre ─────────────────────────── */

  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-theme-toggle]")) return;
    var html = document.documentElement;
    var next = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
    html.setAttribute("data-theme", next);
    try {
      localStorage.setItem("ds-theme", next);
    } catch (err) {
      /* stockage indisponible */
    }
  });

  /* « Système » se traduit par l'absence de préférence stockée : il faut alors
     suivre le réglage du système, y compris quand il change en cours de session. */
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function (e) {
    var stored = null;
    try {
      stored = localStorage.getItem("ds-theme");
    } catch (err) {
      /* stockage indisponible */
    }
    if (stored === "light" || stored === "dark") return;
    document.documentElement.setAttribute("data-theme", e.matches ? "dark" : "light");
  });

  /* ─────────────────────────── Confirmation ─────────────────────────── */

  var confirmState = { resolve: null, form: null, word: null };

  function confirmEls() {
    return {
      scrim: overlayByName("dsConfirm"),
      title: document.getElementById("dsConfirmTitle"),
      message: document.getElementById("dsConfirmMessage"),
      action: document.getElementById("dsConfirmAction"),
      inputWrap: document.getElementById("dsConfirmInputWrap"),
      inputLabel: document.getElementById("dsConfirmInputLabel"),
      input: document.getElementById("dsConfirmInput"),
    };
  }

  function setupConfirmInput(word) {
    var el = confirmEls();
    confirmState.word = word || null;
    if (!el.inputWrap) return;
    if (confirmState.word) {
      el.inputLabel.textContent =
        "Tapez « " + confirmState.word + " » pour confirmer";
      el.input.value = "";
      el.inputWrap.hidden = false;
      el.action.disabled = true;
    } else {
      el.inputWrap.hidden = true;
      el.action.disabled = false;
    }
  }

  function openConfirm(title, message, actionLabel, word) {
    var el = confirmEls();
    if (!el.scrim) return false;
    el.title.textContent = title;
    el.message.textContent = message || "";
    el.action.textContent = actionLabel || "Supprimer";
    setupConfirmInput(word);
    openOverlay("dsConfirm");
    return true;
  }

  window.dsConfirm = function (title, message, actionLabel) {
    confirmState.form = null;
    if (!openConfirm(title, message, actionLabel, null)) {
      return Promise.resolve(window.confirm(title));
    }
    return new Promise(function (resolve) {
      confirmState.resolve = resolve;
    });
  };

  // Capture : intercepte avant tout autre gestionnaire de soumission.
  document.addEventListener(
    "submit",
    function (e) {
      var form = e.target;
      var title = form.getAttribute("data-confirm-title");
      if (!title) return;
      e.preventDefault();
      confirmState.resolve = null;
      confirmState.form = form;
      openConfirm(
        title,
        form.getAttribute("data-confirm-message"),
        form.getAttribute("data-confirm-action"),
        form.getAttribute("data-confirm-input"),
      );
    },
    true,
  );

  document.addEventListener("input", function (e) {
    if (e.target.id !== "dsConfirmInput" || !confirmState.word) return;
    var el = confirmEls();
    el.action.disabled = e.target.value.trim() !== confirmState.word;
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest("#dsConfirmAction")) return;
    var el = confirmEls();
    var form = confirmState.form;
    var resolve = confirmState.resolve;
    confirmState.form = null;
    confirmState.resolve = null;
    if (el.scrim) closeOverlay(el.scrim);

    if (form) {
      // Retire l'attribut le temps de la soumission pour ne pas se réintercepter.
      var title = form.getAttribute("data-confirm-title");
      form.removeAttribute("data-confirm-title");
      form.requestSubmit();
      form.setAttribute("data-confirm-title", title);
    }
    if (resolve) resolve(true);
  });

  document.addEventListener("overlay:close", function (e) {
    if (e.target !== overlayByName("dsConfirm")) return;
    if (confirmState.resolve) {
      confirmState.resolve(false);
      confirmState.resolve = null;
    }
    confirmState.form = null;
    setupConfirmInput(null);
  });

  /* ─────────────────────────── API publique ─────────────────────────── */

  window.scUI = {
    openOverlay: openOverlay,
    closeOverlay: function (name) {
      var scrim = overlayByName(name);
      if (scrim) closeOverlay(scrim);
    },
  };
})();
