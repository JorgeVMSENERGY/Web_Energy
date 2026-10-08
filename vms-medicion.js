/* vms-medicion.js — capa de datos y consentimiento para GTM-TDZFQ3CJ
 * Se carga en el <head> ANTES del contenedor de GTM. Implementa lo que espera
 * el espacio de trabajo de GTM (export 2026-10-01):
 *   - Consent Mode v2 (default) + banner de cookies
 *   - page_slug, page_concept, atribucion.utm_* (primer toque), consent_*
 *   - eventos: form_submit, cta_click, contacto_telefono|correo|whatsapp,
 *              descarga_material, scroll_depth, engaged_30s, postulacion
 * Política de consentimiento (confirmar con Legal):
 *   - Analítica: activa por defecto, el visitante puede rechazarla (opt-out).
 *   - Mercadotecnia (Google Ads, Meta, LinkedIn): desactivada hasta que el visitante acepte (opt-in).
 */
(function (w, d) {
  'use strict';
  var ANALITICA_POR_DEFECTO = 'granted';   // cambiar a 'denied' para opt-in en analítica
  var KEY_CONSENT = 'vms_consent_v1';
  var KEY_UTM = 'vms_utm_first';
  var AVISO_URL = '/aviso-privacidad.html#sitio-web';

  w.dataLayer = w.dataLayer || [];
  function gtag() { w.dataLayer.push(arguments); }
  function push(o) { try { w.dataLayer.push(o); } catch (x) {} }
  function leer(k) { try { return JSON.parse(w.localStorage.getItem(k)); } catch (x) { return null; } }
  function guardar(k, v) { try { w.localStorage.setItem(k, JSON.stringify(v)); } catch (x) {} }

  /* ---------- 1. Consentimiento (antes de GTM) ---------- */
  var elegido = leer(KEY_CONSENT);   // {analitica:bool, mercadotecnia:bool}
  var analitica = elegido ? (elegido.analitica ? 'granted' : 'denied') : ANALITICA_POR_DEFECTO;
  var mkt = elegido && elegido.mercadotecnia ? 'granted' : 'denied';
  gtag('consent', 'default', {
    analytics_storage: analitica,
    ad_storage: mkt, ad_user_data: mkt, ad_personalization: mkt,
    functionality_storage: 'granted', security_storage: 'granted',
    wait_for_update: 500
  });

  /* ---------- 2. Datos de la página y atribución de primer toque ---------- */
  var path = w.location.pathname;
  var slug = path.replace(/^\/+|\/+$/g, '').replace(/\.html$/i, '') || 'index';
  var concept = d.documentElement.getAttribute('data-concept') || '';
  var qs = new URLSearchParams(w.location.search);
  var utm = leer(KEY_UTM);
  if (!utm && qs.get('utm_source')) {
    utm = { utm_source: qs.get('utm_source'), utm_medium: qs.get('utm_medium') || '', utm_campaign: qs.get('utm_campaign') || '' };
    guardar(KEY_UTM, utm);
  }
  push({
    page_slug: slug,
    page_concept: concept,
    atribucion: utm || { utm_source: '', utm_medium: '', utm_campaign: '' },
    consent_analitica: analitica,
    consent_mercadotecnia: mkt
  });

  /* ---------- 3. Formularios ---------- */
  // Recuerda el último formulario enviado; los formularios llaman vmsLead()
  // justo antes de abrir el correo (mailto:). Mide intención de envío.
  d.addEventListener('submit', function (e) { w.__vmsLastForm = e.target; }, true);
  w.vmsLead = function (metodo) {
    var f = w.__vmsLastForm || {};
    push({ event: 'form_submit', form_id: f.id || '', form_metodo: metodo || 'mailto' });
  };
  w.vmsPostulacion = function (formId, metodo) {
    push({ event: 'postulacion', form_id: formId || '', form_metodo: metodo || 'api' });
  };

  /* ---------- 4. Clics: CTA, contacto directo, descargas ---------- */
  var EXT = /\.(pdf|docx?|xlsx?|pptx?|zip|dwg)(?:[?#]|$)/i;
  d.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('a,[data-cta]') : null;
    if (!el) return;
    var href = el.getAttribute('href') || '';
    if (el.hasAttribute('data-cta')) {
      push({ event: 'cta_click', cta: el.getAttribute('data-cta'),
             cta_texto: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 100), href: href });
    }
    if (/^tel:/i.test(href)) {
      push({ event: 'contacto_telefono', metodo: 'telefono', valor: href.slice(4) });
    } else if (/^mailto:/i.test(href)) {
      push({ event: 'contacto_correo', metodo: 'correo', valor: href.slice(7).split('?')[0] });
    } else if (/^https?:\/\/(wa\.me|api\.whatsapp\.com|web\.whatsapp\.com)\//i.test(href)) {
      push({ event: 'contacto_whatsapp', metodo: 'whatsapp', valor: href.split('?')[0] });
    } else if (EXT.test(href)) {
      var archivo = decodeURIComponent(href.split(/[?#]/)[0].split('/').pop());
      push({ event: 'descarga_material', archivo: archivo, tipo_archivo: archivo.split('.').pop().toLowerCase() });
    }
  }, true);

  /* ---------- 5. Profundidad de scroll y 30 s de atención ---------- */
  var marcas = [25, 50, 75, 90], hechas = {};
  function scroll() {
    var h = d.documentElement.scrollHeight - w.innerHeight;
    if (h <= 0) return;
    var pct = (w.scrollY / h) * 100;
    for (var i = 0; i < marcas.length; i++) {
      if (pct >= marcas[i] && !hechas[marcas[i]]) { hechas[marcas[i]] = 1; push({ event: 'scroll_depth', porcentaje: marcas[i] }); }
    }
  }
  w.addEventListener('scroll', scroll, { passive: true });
  var visible = 0, enviado = false;
  var timer = w.setInterval(function () {
    if (d.visibilityState === 'visible') visible++;
    if (visible >= 30 && !enviado) { enviado = true; w.clearInterval(timer); push({ event: 'engaged_30s' }); }
  }, 1000);

  /* ---------- 6. Banner de cookies ---------- */
  function aplicar(a, m) {
    guardar(KEY_CONSENT, { analitica: a, mercadotecnia: m, fecha: new Date().toISOString() });
    var A = a ? 'granted' : 'denied', M = m ? 'granted' : 'denied';
    gtag('consent', 'update', { analytics_storage: A, ad_storage: M, ad_user_data: M, ad_personalization: M });
    push({ event: 'consent_update', consent_analitica: A, consent_mercadotecnia: M });
  }
  var CSS = '.vms-ck{position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;max-width:720px;margin:0 auto;' +
    'background:#fff;color:#1f2937;border:1px solid #d1d5db;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.18);' +
    'padding:18px 20px;font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}' +
    '.vms-ck h2{margin:0 0 6px;font-size:16px;color:#172C51}.vms-ck p{margin:0 0 12px}.vms-ck a{color:#0F4C81}' +
    '.vms-ck__opts{display:none;margin:0 0 12px;padding:10px 12px;background:#f3f4f6;border-radius:8px}' +
    '.vms-ck__opts label{display:block;margin:4px 0}.vms-ck--cfg .vms-ck__opts{display:block}' +
    '.vms-ck__btns{display:flex;flex-wrap:wrap;gap:8px}.vms-ck button{cursor:pointer;border-radius:6px;padding:8px 14px;' +
    'font:600 14px system-ui,sans-serif;border:1px solid #0F4C81;background:#fff;color:#0F4C81}' +
    '.vms-ck button.vms-ck__ok{background:#0F4C81;color:#fff}.vms-ck-link{cursor:pointer;background:none;border:0;' +
    'padding:0;color:inherit;font:inherit;text-decoration:underline;opacity:.85}';
  function banner() {
    if (d.getElementById('vmsCookies')) return;
    var actual = leer(KEY_CONSENT) || { analitica: ANALITICA_POR_DEFECTO === 'granted', mercadotecnia: false };
    var box = d.createElement('div');
    box.className = 'vms-ck'; box.id = 'vmsCookies';
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Preferencias de cookies');
    box.innerHTML = '<h2>Cookies en vmsenergy.com</h2>' +
      '<p>Usamos cookies de analítica para entender cómo se usa el sitio y, si lo aceptas, cookies de mercadotecnia ' +
      '(Google Ads, Meta y LinkedIn) para medir nuestras campañas. Más información en el <a href="' + AVISO_URL + '">aviso de privacidad</a>.</p>' +
      '<div class="vms-ck__opts">' +
      '<label><input type="checkbox" checked disabled> Necesarias (siempre activas)</label>' +
      '<label><input type="checkbox" id="vmsCkA"' + (actual.analitica ? ' checked' : '') + '> Analítica (Google Analytics)</label>' +
      '<label><input type="checkbox" id="vmsCkM"' + (actual.mercadotecnia ? ' checked' : '') + '> Mercadotecnia (Google Ads, Meta, LinkedIn)</label></div>' +
      '<div class="vms-ck__btns"><button type="button" class="vms-ck__ok" data-ck="todas">Aceptar todas</button>' +
      '<button type="button" data-ck="necesarias">Solo necesarias</button>' +
      '<button type="button" data-ck="config">Configurar</button></div>';
    box.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var k = b.getAttribute('data-ck');
      if (k === 'todas') aplicar(true, true);
      else if (k === 'necesarias') aplicar(false, false);
      else if (k === 'config') { box.classList.add('vms-ck--cfg'); b.textContent = 'Guardar selección'; b.setAttribute('data-ck', 'guardar'); return; }
      else if (k === 'guardar') aplicar(d.getElementById('vmsCkA').checked, d.getElementById('vmsCkM').checked);
      box.parentNode.removeChild(box);
    });
    d.body.appendChild(box);
  }
  w.vmsCookies = banner;   // reabrir desde cualquier enlace: onclick="vmsCookies()"
  d.addEventListener('DOMContentLoaded', function () {
    var st = d.createElement('style'); st.textContent = CSS; d.head.appendChild(st);
    var pie = d.querySelector('.site-footer');
    if (pie) {
      var p = d.createElement('p'); p.style.cssText = 'text-align:center;margin:12px 0 0;font-size:13px';
      p.innerHTML = '<button type="button" class="vms-ck-link">Preferencias de cookies</button>';
      p.firstChild.addEventListener('click', banner);
      pie.appendChild(p);
    }
    if (!leer(KEY_CONSENT)) banner();
  });
})(window, document);
