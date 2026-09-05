"use strict";

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((other) => {
      const active = other === button;
      other.classList.toggle("a", active);
      other.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll(".tc").forEach((panel) => {
      panel.classList.toggle("a", panel.id === "tab-" + button.dataset.tab);
    });
  });
});

document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    const container = button.closest(".tp");
    container.querySelectorAll("[data-filter]").forEach((other) => {
      const active = other === button;
      other.classList.toggle("a", active);
      other.setAttribute("aria-pressed", String(active));
    });
    container.querySelectorAll(".li").forEach((row) => {
      row.hidden = button.dataset.filter !== "all" && row.dataset.s !== button.dataset.filter;
    });
  });
});

function updateStaleness() {
  const last = Date.parse(document.body.dataset.lastSuccess);
  const now = new Date();
  const jst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  const weekday = jst.getUTCDay() >= 1 && jst.getUTCDay() <= 5;
  const hour = jst.getUTCHours();
  // Grace period after the morning start; do not warn during scheduled downtime.
  const expected = weekday && hour >= 8 && hour <= 20;
  document.getElementById("stale-warning").hidden =
    !(expected && Number.isFinite(last) && now.getTime() - last > 90 * 60 * 1000);
}
updateStaleness();
setInterval(updateStaleness, 60 * 1000);
