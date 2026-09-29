// Palinsesto: due comodita', la pagina funziona anche senza.
// 1) conferma prima delle azioni distruttive (form.conferma[data-conferma]);
// 2) dopo un clic su un interruttore la pagina si ricarica: torna allo stesso punto
//    invece che in cima, che sul telefono vuol dire perdere il segno.
document.addEventListener("submit", (e) => {
  const f = e.target;
  if (f.classList.contains("conferma") && !confirm(f.dataset.conferma)) {
    e.preventDefault();
    return;
  }
  if ((f.method || "").toLowerCase() === "post") {
    try { sessionStorage.setItem("pal-scroll", JSON.stringify([location.pathname, window.scrollY])); } catch (_) {}
  }
});
window.addEventListener("DOMContentLoaded", () => {
  let v = null;
  try { v = JSON.parse(sessionStorage.getItem("pal-scroll")); sessionStorage.removeItem("pal-scroll"); } catch (_) {}
  if (v && v[0] === location.pathname && !location.hash) window.scrollTo(0, v[1]);
});
// dopo una spunta si torna su #sN: quella stagione resta aperta
window.addEventListener("DOMContentLoaded", () => {
  // solo #s1, #s2...: «#stagioni» e' la sezione intera, e aprirebbe la prima stagione
  const li = /^#s\d+$/.test(location.hash) && document.getElementById(location.hash.slice(1));
  const d = li && li.querySelector("details");
  if (d) d.open = true;
});
// mentre un giro gira (Impostazioni), la pagina si ricarica da sola e torna alla
// sezione: il link a «#aggiorna» dalla stessa pagina scorreva e basta (29/09)
window.addEventListener("DOMContentLoaded", () => {
  const el = document.querySelector("[data-ricarica]");
  if (!el) return;
  setTimeout(() => { location.replace(location.pathname + "?t=" + Date.now() + "#aggiorna"); }, 1000 * Number(el.dataset.ricarica || 10));
});
