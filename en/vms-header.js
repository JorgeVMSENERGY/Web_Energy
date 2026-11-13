/*
 * ============================================================
 *  vms-header.js — Encabezado VMS Energy
 *  Versión 1.0 · agosto 2026 · primer módulo de vms-core.js
 * ============================================================
 *
 *  Reemplaza los bloques de menú repetidos en 89 páginas. Antes cada
 *  página traía su propia copia de estas funciones, y unas 20 de ellas
 *  redefinían los mismos helpers ($, $$) una y otra vez.
 *
 *  Hace tres cosas y nada más:
 *   1. Marca el ítem de la página actual (antes iba escrito a mano
 *      como "✓ Minería" dentro del HTML, y por eso cada página
 *      contaba como una variante distinta del menú).
 *   2. Abre y cierra el menú móvil.
 *   3. Abre y cierra los submenús en táctil, donde el hover no sirve.
 *
 *  Sin dependencias. Cargar con defer:
 *     <script src="vms-header.js" defer></script>
 * ============================================================ */

(function () {
  'use strict';

  // Also enable language navigation on pages managed through the staging workflow.
  if (!document.querySelector('script[src$="vms-language.js"]')) {
    var languageScript = document.createElement('script');
    languageScript.src = new URL('../vms-language.js', document.currentScript.src).href;
    document.head.appendChild(languageScript);
  }

  var header = document.querySelector('.site-header');
  if (!header) return;

  var toggle = header.querySelector('#menuToggle');
  var nav    = header.querySelector('.site-nav');
  var MOBILE = '(max-width: 1080px)';

  var isMobile = function () { return window.matchMedia(MOBILE).matches; };

  /* ── 1 · Página actual ──────────────────────────────────────
     Compara solo el nombre de archivo, así funciona igual en el
     prototipo (pagina.html) y en WordPress (/pagina/).           */
  function currentSlug(url) {
    try {
      var path = new URL(url, window.location.origin).pathname;
      var last = path.replace(/\/+$/, '').split('/').pop() || 'index.html';
      return last.replace(/\.html$/i, '').toLowerCase();
    } catch (e) { return ''; }
  }

  var here = currentSlug(window.location.href);

  header.querySelectorAll('a[href]').forEach(function (a) {
    if (a.classList.contains('lang-switch')) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;
    if (currentSlug(href) !== here) return;

    a.setAttribute('aria-current', 'page');

    // Si el activo está dentro de un submenú, marcar también su cabeza.
    var item = a.closest('.nav-item');
    if (item) {
      var head = item.querySelector(':scope > a');
      if (head && head !== a) head.setAttribute('aria-current', 'true');
    }
  });

  /* ── 2 · Menú móvil ─────────────────────────────────────────── */
  function closeMenu() {
    header.classList.remove('site-header--open');
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  }

  if (toggle) {
    toggle.addEventListener('click', function () {
      var open = header.classList.toggle('site-header--open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  /* ── 3 · Submenús ───────────────────────────────────────────
     En escritorio los abre el CSS con :hover y :focus-within.
     Aquí solo se atiende el táctil, donde el hover deja el menú
     pegado abierto: el primer toque abre, el segundo navega.     */
  header.querySelectorAll('.nav-item > a[aria-haspopup="true"]').forEach(function (head) {
    head.addEventListener('click', function (ev) {
      if (!isMobile() && !window.matchMedia('(hover: none)').matches) return;

      var open = head.getAttribute('aria-expanded') === 'true';
      if (!open) {
        ev.preventDefault();
        closeSiblings(head);
        head.setAttribute('aria-expanded', 'true');
      }
      // Segundo toque: no se previene y el enlace navega.
    });
  });

  function closeSiblings(except) {
    header.querySelectorAll('.nav-item > a[aria-expanded="true"]').forEach(function (a) {
      if (a !== except) a.setAttribute('aria-expanded', 'false');
    });
  }

  /* ── Cierre por Escape y por clic fuera ─────────────────────── */
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    closeSiblings(null);
    closeMenu();
  });

  document.addEventListener('click', function (ev) {
    if (header.contains(ev.target)) return;
    closeSiblings(null);
    closeMenu();
  });

  /* Al pasar de móvil a escritorio, limpiar el estado móvil para que
     el CSS vuelva a mandar. Sin esto el menú queda abierto al girar
     la tableta. */
  window.matchMedia(MOBILE).addEventListener('change', function (e) {
    if (!e.matches) { closeSiblings(null); closeMenu(); }
  });

  /* ── Sombra al hacer scroll ─────────────────────────────────── */
  var ticking = false;
  window.addEventListener('scroll', function () {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(function () {
      header.classList.toggle('site-header--scrolled', window.scrollY > 8);
      ticking = false;
    });
  }, { passive: true });

})();


/* ---- Consentimiento de datos personales (25-ago-2026) ------------------
   Guarda unica, en fase de captura: se ejecuta ANTES del manejador propio de
   cada formulario, asi que ninguno de los 28 hubo que modificarlo. Si la
   casilla no esta marcada, el envio se detiene aqui. */
(function(){
  function caja(el){ return el.closest ? el.closest('.form-consent') : null; }
  document.addEventListener('submit', function(e){
    var f = e.target;
    if(!f || f.tagName !== 'FORM') return;
    var chk = f.querySelector('input[type="checkbox"][name="privacidad"]');
    if(!chk || chk.checked) return;
    e.preventDefault();
    e.stopPropagation();
    var c = caja(chk); if(c) c.classList.add('is-missing');
    try { chk.focus(); } catch(err){}
  }, true);
  document.addEventListener('change', function(e){
    var t = e.target;
    if(t && t.name === 'privacidad' && t.checked){
      var c = caja(t); if(c) c.classList.remove('is-missing');
    }
  }, true);
})();
