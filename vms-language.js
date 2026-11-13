/* Language navigation shared by the Spanish site and its static English mirror. */
(function () {
  'use strict';
  var script = document.currentScript;
  var root = new URL('.', script.src);
  var aliases = {
    'soluciones_oil_gas.html': 'oil-gas-solutions.html',
    'lp-mantenimiento-oil-gas.html': 'oil-gas-maintenance.html'
  };
  var reverse = {};
  Object.keys(aliases).forEach(function (key) { reverse[aliases[key]] = key; });
  var english = document.documentElement.lang.toLowerCase().indexOf('en') === 0;
  var page = decodeURIComponent(location.pathname.split('/').pop() || 'index.html');
  var target = english ? (reverse[page] || page) : (aliases[page] || page);
  var path = english ? target : 'en/' + (target === 'index.html' ? '' : target);
  var url = new URL(path, root);
  url.search = location.search;
  url.hash = location.hash;
  if (!english && !document.querySelector('link[rel="alternate"][hreflang="en"]')) {
    var alternate = document.createElement('link');
    alternate.rel = 'alternate';
    alternate.hreflang = 'en';
    alternate.href = new URL('en/' + (aliases[page] || page), root).href;
    document.head.appendChild(alternate);
  }

  var links = document.querySelectorAll('.lang-switch, [data-language-switch]');
  if (!links.length) {
    var list = document.querySelector('.site-header nav > ul, header nav > ul, #menuList');
    if (!list) return;
    var item = document.createElement('li');
    var link = document.createElement('a');
    link.className = 'lang-switch';
    link.style.cssText = 'font-size:.82rem;letter-spacing:.5px;border:1px solid currentColor;border-radius:999px;padding:.32rem .62rem;font-weight:700';
    item.appendChild(link);
    var contact = list.querySelector('.cta-header');
    list.insertBefore(item, contact ? contact.closest('li') : null);
    links = [link];
  }
  Array.prototype.forEach.call(links, function (link) {
    link.href = url.href;
    link.hreflang = english ? 'es-MX' : 'en';
    link.lang = english ? 'es' : 'en';
    link.textContent = english ? 'ES' : 'EN';
    link.title = english ? 'View this page in Spanish' : 'Ver esta página en inglés';
    link.setAttribute('aria-label', link.title);
    link.removeAttribute('aria-current');
  });
})();
