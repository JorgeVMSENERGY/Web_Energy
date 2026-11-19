/* Localize DOM output while preserving original data, filter keys and form values.
 * The dictionary is generated at authoring time; no visitor text leaves the browser.
 */
(function () {
  'use strict';
  var dictionary = window.VMS_EN_TRANSLATIONS || {};
  var pages = window.VMS_EN_PAGES || {};
  var root = new URL('../', document.currentScript.src);
  var words = Object.keys(dictionary).filter(function (key) {
    return key !== dictionary[key] && key.length > 2 && key.length <= 250;
  }).sort(function (a, b) { return b.length - a.length; });
  var escape = function (text) { return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); };
  var phrases = words.length ? new RegExp('(^|[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_])(' +
    words.map(escape).join('|') + ')(?=$|[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_])', 'g') : null;
  var normalize = function (text) { return text.replace(/\s+/g, ' ').trim(); };
  var englishCopy = new Set(Object.keys(dictionary).map(function (key) { return normalize(dictionary[key]); }));
  var identifiers = /((?:https?:\/\/[^\s]+)|(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})|(?:\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b))/g;
  function translate(text) {
    var key = normalize(text);
    if (!key) return text;
    if (englishCopy.has(key)) return text;
    var exact = dictionary[key];
    var result = exact === undefined ? key.split(identifiers).map(function (part, index) {
      if (index % 2 || !phrases) return part;
      return part.replace(phrases, function (_, boundary, phrase) { return boundary + dictionary[phrase]; });
    }).join('') : exact;
    return text.match(/^\s*/)[0] + result + text.match(/\s*$/)[0];
  }
  window.vmsTranslate = translate;

  var attributes = ['alt', 'title', 'placeholder', 'aria-label', 'aria-description', 'data-vms-success-message'];
  var skip = 'script,style,code,pre,textarea,[translate="no"],.lang-switch';
  function textNode(node) {
    var parent = node.parentElement;
    if (!parent || parent.closest(skip)) return;
    // Changing a label must not change the filter/API value of an option.
    if (parent.tagName === 'OPTION' && !parent.hasAttribute('value')) {
      parent.setAttribute('value', parent.textContent);
    }
    var next = translate(node.nodeValue);
    if (next !== node.nodeValue) node.nodeValue = next;
  }
  function element(node) {
    if (node.matches(skip)) return;
    if (node.tagName === 'A' && node.hasAttribute('href')) {
      var url = new URL(node.getAttribute('href'), document.baseURI);
      var relative = url.pathname.indexOf(root.pathname) === 0 ? url.pathname.slice(root.pathname.length) : '';
      if (url.origin === root.origin && pages[relative || 'index.html'] && relative.indexOf('en/') !== 0) {
        url.pathname = root.pathname + 'en/' + pages[relative || 'index.html'];
        if (node.href !== url.href) node.href = url.href;
      }
    }
    ['src', 'poster'].forEach(function (name) {
      var value = node.getAttribute(name);
      if (value && value.indexOf('assets/') === 0 && value.indexOf('assets/data/') !== 0) {
        node.setAttribute(name, '../' + value);
      }
    });
    attributes.forEach(function (name) {
      if (!node.hasAttribute(name)) return;
      var previous = node.getAttribute(name);
      var next = translate(previous);
      if (next !== previous) node.setAttribute(name, next);
    });
  }
  function scan(node) {
    if (node.nodeType === 3) { textNode(node); return; }
    if (node.nodeType !== 1 || node.matches(skip)) return;
    element(node);
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    var current;
    while ((current = walker.nextNode())) {
      if (current.nodeType === 3) textNode(current);
      else element(current);
    }
  }
  var observer = new MutationObserver(function (records) {
    // Disconnect while writing so translated English cannot be translated a second time.
    observer.disconnect();
    records.forEach(function (record) {
      if (record.type === 'characterData') textNode(record.target);
      else if (record.type === 'attributes') element(record.target);
      else record.addedNodes.forEach(scan);
    });
    observe();
  });
  function observe() {
    observer.observe(document.documentElement, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: attributes.concat(['href', 'src', 'poster'])
    });
  }
  observe();
})();
