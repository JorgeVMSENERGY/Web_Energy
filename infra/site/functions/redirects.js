// CloudFront Function (viewer-request) — redirecciones 301 de vmsenergy.com
// Fuente: auditoría redirecciones-301.xlsx (Search Console + GA4, 2026-07-01 a 2026-09-29)
// + exports de Search Console del 2 oct 2026 (cobertura 403/404)
// Incluye filas "Directa", "Sugerida" y "Decidir", todas resueltas por Marketing
// el 1 oct 2026. Sigue fuera a propósito: todo /en/ (pendiente de decidir).
// Las claves van sin diagonal final y en minúsculas; la función normaliza la URI.

var CANONICAL_HOST = 'vmsenergy.com';

var REDIRECTS = {
  // Directa
  '/celdas-de-media-tension': '/lp-celdas-media-tension.html',
  '/nosotros': '/nosotros.html',
  '/vacantes': '/carreras.html',
  '/ingenieria': '/especialidad_ingenieria.html',
  '/mantenimiento-y-reparacion-de-subestaciones-electricas-at-mt-y-bt-en-mexico': '/blog-mantenimiento-reparacion-subestaciones-at-mt-bt.html',
  '/soluciones': '/soluciones.html',
  '/la-importancia-de-los-tableros': '/blog-importancia-tableros-distribucion.html',
  '/la-importancia-de-los-tableros-de-distribucion-en-los-sistemas-electricos': '/blog-importancia-tableros-distribucion.html',
  '/caso-de-exito-ctg-chankanaab': '/blog-caso-exito-ctg-chankanaab.html',
  '/variadores-en-la-industria-minera': '/blog-variadores-industria-minera.html',
  '/variadores-industria-minera': '/blog-variadores-industria-minera.html',
  '/caso-de-exito-embotelladora-coca-cola-de-colima-2022': '/blog-caso-exito-embotelladora-colima-2022.html',
  '/variador-regenerativo-ingenio-tamazula': '/blog-variador-regenerativo-ingenio-tamazula.html',
  '/subestacion-electrica-industrial': '/blog-subestacion-electrica-industrial.html',
  '/blog': '/blog.html',
  '/bombeo-en-mineria-subterranea-desague-critico-a-900-m-en-durango': '/blog-bombeo-mineria-subterranea-durango.html',

  // Sugerida (destino confirmado)
  '/directorio': '/directorio.html',
  '/servicios': '/especialidades.html',
  '/componentes-variador-vfd': '/soluciones-variadores-frecuencia.html',

  // Institucionales (páginas con noindex, fuera del sitemap a propósito)
  '/aviso-privacidad': '/aviso-privacidad.html',
  '/codigo-de-etica': '/codigo-conducta.html',
  '/buzon-de-sugerencias': '/buzon-sugerencias.html',

  // Decididas por Marketing
  // VMS no es distribuidor: la intención de /productos/ la cubre Procura.
  '/productos': '/procura.html',
  // Sin cartas para usar logotipos: los aliados viven en la sección de la home.
  '/aliados': '/#clientesPorSectorAliados',
  // El PDF viejo de WordPress va a la sección del informe, que tiene la descarga.
  '/wp-content/uploads/2026/01/informe-de-sostentabilidad26_comprimido_compressed.pdf': '/sostenibilidad.html#informe',

  // Search Console 2 oct 2026 (403/404/sin indexar que faltaban)
  '/mantenimiento': '/especialidad_mantenimiento.html',
  '/politica-antilavado': '/politica-interna_antilavado.html',
  '/oil&gas': '/soluciones_oil_gas.html',
  '/oil%26gas': '/soluciones_oil_gas.html',
  '/wp-content/uploads/2026/02/codigo-de-etica-y-conducta2026.pdf': '/codigo-conducta.html',
  '/wp-content/uploads/2025/04/informe-de-sostenibilidad_comprimido.pdf': '/sostenibilidad.html#informe'
};

// Páginas de autor, etiqueta y categoría de WordPress → blog.html
var WP_ARCHIVE = /^(\/en)?\/(author|tag|category)(\/|$)/;

function qs(querystring) {
  var parts = [];
  for (var k in querystring) {
    var p = querystring[k];
    if (p.multiValue) {
      for (var i = 0; i < p.multiValue.length; i++) parts.push(k + '=' + p.multiValue[i].value);
    } else {
      parts.push(p.value === '' ? k : k + '=' + p.value);
    }
  }
  return parts.length ? '?' + parts.join('&') : '';
}

function redirect(location) {
  return {
    statusCode: 301,
    statusDescription: 'Moved Permanently',
    headers: {
      location: { value: location },
      'cache-control': { value: 'max-age=86400' }
    }
  };
}

function handler(event) {
  var request = event.request;
  var host = request.headers.host ? request.headers.host.value.toLowerCase() : CANONICAL_HOST;
  var uri = request.uri;
  var key = uri.toLowerCase();
  if (key.length > 1 && key.charAt(key.length - 1) === '/') key = key.slice(0, -1);

  var target = REDIRECTS[key];
  if (!target && WP_ARCHIVE.test(key)) target = '/blog.html';

  // Un solo salto: www + URL vieja va directo al destino final sin www.
  if (target) return redirect('https://' + CANONICAL_HOST + target + qs(request.querystring));
  if (host !== CANONICAL_HOST) return redirect('https://' + CANONICAL_HOST + uri + qs(request.querystring));

  // /en/ existe como en/index.html, pero CloudFront solo resuelve index.html en la raíz
  // (S3 devolvía 403). /en → /en/ y /en/ se sirve desde /en/index.html.
  if (uri === '/en') return redirect('https://' + CANONICAL_HOST + '/en/' + qs(request.querystring));
  if (uri === '/en/') request.uri = '/en/index.html';

  return request;
}
