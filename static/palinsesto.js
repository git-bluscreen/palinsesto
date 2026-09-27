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
