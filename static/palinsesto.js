// Palinsesto: poche comodita', la pagina funziona anche senza.
// 0) il token di accesso dura pochi minuti: la pagina ne chiede uno nuovo poco
//    prima che scada, e subito quando torna in primo piano;
// 1) conferma prima delle azioni distruttive (form.conferma[data-conferma]);
// 2) dopo un clic su un interruttore la pagina si ricarica: torna allo stesso punto
//    invece che in cima, che sul telefono vuol dire perdere il segno.
const accesso = (() => {
  const m = document.querySelector('meta[name="pal-restano"]');
  // il limite si calcola sull'orologio di questo dispositivo: se e' avanti o
  // indietro rispetto al server non importa
  let limite = m ? Date.now() + 1000 * Number(m.content) : 0;
  let timer = null, attesa = null;
  const ANTICIPO = 60 * 1000;
  const programma = () => {
    clearTimeout(timer);
    timer = setTimeout(rinnova, Math.max(0, limite - Date.now() - ANTICIPO));
  };
  function rinnova() {
    if (attesa) return attesa;              // due richieste insieme ruoterebbero due volte
    attesa = fetch("/token/rinnova", { method: "POST", credentials: "same-origin", headers: { "X-Palinsesto": "rinnovo" } })
      .then((r) => {
        if (r.status === 401) {             // rinnovo scaduto o dispositivo chiuso: di nuovo password e codice
          location.href = "/accesso?dopo=" + encodeURIComponent(location.pathname + location.search);
          return false;
        }
        if (!r.ok) throw new Error(r.status);
        return r.json().then((j) => { limite = Date.now() + 1000 * j.restano; programma(); return true; });
      })
      .catch(() => {                        // rete assente (fuori casa senza VPN): si riprova, senza buttare fuori
        clearTimeout(timer);
        timer = setTimeout(rinnova, 30 * 1000);
        return false;
      })
      .finally(() => { attesa = null; });
    return attesa;
  }
  const daRinnovare = () => !!m && Date.now() > limite - ANTICIPO;
  if (m) {
    programma();
    // in background i timer rallentano o si fermano: al ritorno si controlla subito
    document.addEventListener("visibilitychange", () => { if (!document.hidden && daRinnovare()) rinnova(); });
    window.addEventListener("pageshow", (e) => { if (e.persisted && daRinnovare()) rinnova(); });
  }
  return { rinnova, daRinnovare };
})();
document.addEventListener("submit", (e) => {
  const f = e.target;
  const ripreso = f._pal_ripreso;           // reinviato dopo il rinnovo: la conferma c'e' gia' stata
  f._pal_ripreso = false;
  if (!ripreso && f.classList.contains("conferma") && !confirm(f.dataset.conferma)) {
    e.preventDefault();
    return;
  }
  // token di accesso scaduto o quasi (telefono in tasca, scheda dimenticata):
  // prima si rinnova, poi il modulo parte con lo stesso tasto
  if (!ripreso && accesso.daRinnovare()) {
    e.preventDefault();
    const tasto = e.submitter;
    accesso.rinnova().then((ok) => { if (ok) { f._pal_ripreso = true; f.requestSubmit(tasto); } });
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
