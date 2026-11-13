"""Build the static English mirror. Translation is an explicit, cached authoring step.

python scripts/build_english.py --collect
python scripts/build_english.py --translate  # public website copy only; requires network
python scripts/build_english.py --build      # deterministic, offline
"""
from __future__ import annotations

import argparse
import csv
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import subprocess
import time
import os
import sys
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Authoring-only parser; install scripts/requirements-english.txt.
temporary_tools = Path(os.environ.get('TEMP', '/tmp')) / 'vms-translation-tools'
if temporary_tools.exists():
    sys.path.insert(0, str(temporary_tools))
try:
    import esprima
except ImportError:
    esprima = None

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'scripts' / 'english-translations.json'
TRACKED = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0'))
PAGES = sorted(p for p in ROOT.glob('*.html') if p.name in TRACKED and p.name != 'stand-rim-2026 3.html')
NAMES = {p.name for p in PAGES}
ALIASES = {'soluciones_oil_gas.html': 'oil-gas-solutions.html',
           'lp-mantenimiento-oil-gas.html': 'oil-gas-maintenance.html'}
ORIGIN = 'https://vmsenergy.com/'
ATTRS = {'alt', 'title', 'placeholder', 'aria-label', 'aria-description',
         'data-vms-success-message', 'data-label', 'data-title', 'data-description',
         'data-caption-title', 'data-caption-text', 'data-diff-title', 'data-diff-text',
         'data-desc', 'data-nombre',
         'data-tooltip', 'data-tooltip-content', 'data-original-title', 'data-loading-text',
         'data-error-text', 'data-success-text', 'data-message', 'data-help', 'data-empty-message'}
META = {'description', 'og:title', 'og:description', 'twitter:title', 'twitter:description'}
URL_ATTRS = {'href', 'src', 'poster', 'action', 'data-src', 'data-poster'}
ATTR_RE = re.compile(r'''(?P<name>[\w:-]+)\s*=\s*(?P<q>["'])(?P<value>.*?)(?P=q)''', re.S)
SPANISH = re.compile(r'[áéíóúüñ¿¡]|\b(?:el|la|los|las|del|para|por|con|sin|una|uno|su|sus|tu|tus|que|desde|hasta|selecciona|seleccione|enviar|enviando|enviado|nombre|correo|empresa|teléfono|telefono|ciudad|estado|país|pais|mensaje|buscar|cerrar|abrir|cargar|cargando|ver|descargar|todos|todas|ninguno|ninguna|no|sí|si|inicio|volver|siguiente|anterior|proyectos|proyecto|servicios|servicio|ingeniería|mantenimiento|soluciones|vacantes|ubicación|requerido|requerida|contacto|cantidad|precio|total|subtotal|producto|productos|carrito|comprar|cotizar|cotización|acepto|aceptar|aviso|privacidad|tensión|potencia|cable|cables|equipo|equipos|capacidad|sector|fecha|cliente|aplicar|limpiar|filtros|filtro|resultados|resultado|detalle|detalles|disponible|disponibles|error|completa|intenta|revisa|escribe|ingresa|adjunta|opcional|obligatorio|obligatoria)\b', re.I)
SPANISH_EXTRA = re.compile(r'\b(?:sistemas?|suministros?|sincronia|cogeneracion|monitoreo|bombeo|puesta|tierra|tierras|relaves|almacenamiento|seguridad|acceso|sobre|sobrecalentamiento|sobrecarga|sobrecorriente|salida|sobredimensionamiento|solar|fotovoltaica|soldada|longitudinalmente|solicitar|solicita|solicitamos|solicitado|siempre|significado|pago|pagos|cerrado|abierto|armadura|sugerencias|canal|canales|sede|oficina|ambiente|paso|pasos|carrera|tipo|tipos|cuerpo|cuerpos|materiales|cobre|aluminio|acero|negro|rojo|azul|verde|amarillo|blanco|naranja|gris|etapa|emergencia|plantas?|sustentable|distribucion|aislamiento|conductor|conductores|tendido|maniobra|maniobras|tableros?|tablero|celda|celdas|transformadores?|motores?|protecciones|estudios?|normas?|pruebas|aceite|humedad|acidez|buenas|buena|buen|bien|regular|malo|mala|riesgo|critico|critica|alta|baja|alto|bajo|media|medio|todas?|todos?|terminado|proceso|tecnico|tecnica|aplicaciones?|arranque|generadores?|compra|costo|ahorro|anual|anuales|mensual|mes|meses|carga|cargas|tasa|tasa|tamaño|tamano|ancho|alto|largo|lugar|fuera|dentro|reintentar|gracias|sostenibilidad|sustentabilidad|resumen|operacion|operaciones|apertura|cierre|potencia|datos|orientacion|capacitores|fuente|confiabilidad|calidad|entrega|condiciones|necesitas|nosotros|procura|energia|tiempo|gobierno|ciencia|salud|empleo|exterior|interior|equipamiento|entorno|ramal|ramales|longitud|fases|fase|año|horas?|hora|dia|dias|fabricante|marca|marcas|familias?|categoria|unidades?|seccion|velocidad|regenerativo|variadores?|referencia|alcance|certificado|conectada|desconectada|conectado|desconectado)\b', re.I)


def spanish_copy(value):
    return human(value) and bool(SPANISH.search(value) or SPANISH_EXTRA.search(value) or re.search(
        r'\b(?:hola|mundo|subestaciones?|industrializacion|accionamientos?|alimentadores?|alimentacion|iluminacion|electrico|electrica|electromecanica|construccion|certificaciones?|autorizacion|reparacion|seleccion|evaluacion|facturacion|recomendado|recomendada|amarillo|tensores?|terminaciones?|turbogeneradores?|cotizaciones|accion|acciones|aceptado|solicitud|interruptores?|seccionadores?|hidrocarburos|extraccion|perforacion|transmision|distribucion|primario|secundario|uniforme|trabajos?|transporte|urgente|utilidad|usuarios?|ventajas|ventilacion|verificacion|verificar|voltaje|zonas?|aerogeneradores?)\b', value, re.I))
MACHINE_KEYS = {'id', 'slug', 'key', 'code', 'codigo', 'sku', 'model', 'modelo',
                'url', 'href', 'src', 'image', 'imagen', 'img', 'foto', 'icon', 'icono',
                'email', 'correo', 'phone', 'telefono', 'tel', 'whatsapp', 'file',
                'archivo', 'path', 'ruta', 'type', '@type', '@context', 'inLanguage',
                'unit', 'unidad', 'currency', 'moneda', 'status', 'estado_id'}


def normalize(text):
    return re.sub(r'\s+', ' ', html.unescape(text)).strip()


def write_utf8(path, text):
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write('\n'.join(line.rstrip() for line in text.splitlines()).rstrip() + '\n')


NUMBER = re.compile(r'(?<!\d)(?:\d{1,3}(?:[ \u00a0\u202f]\d{3}(?!\d))+(?:\.\d+)?|\d+(?:[.,]\d+)*)')
PHONE = re.compile(r'\+\d[\d ,().-]{7,}\d')


def unescape_js(value):
    value = re.sub(r'\\u\{([0-9a-fA-F]+)\}|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})',
                   lambda m: chr(int(next(v for v in m.groups() if v is not None), 16)), value)
    if re.search('[\ud800-\udfff]', value):
        value = value.encode('utf-16', 'surrogatepass').decode('utf-16')
    return value


def preserve_numbers(source, target):
    """Keep technical values and years when the translation retains their positions."""
    phones, translated_phones = list(PHONE.finditer(source)), list(PHONE.finditer(target))
    if phones and len(phones) == len(translated_phones):
        for original, translated in reversed(list(zip(phones, translated_phones))):
            target = target[:translated.start()] + original[0] + target[translated.end():]
        return target
    before, after = list(NUMBER.finditer(source)), list(NUMBER.finditer(target))
    if not before or len(before) != len(after):
        return target
    canonical = lambda match: re.sub(r'[ ,\u00a0\u202f]', '', match[0])
    if sorted(map(canonical, before)) == sorted(map(canonical, after)):
        return target  # English may legitimately reorder model numbers and ratings.
    replacements = []
    for original, translated in zip(before, after):
        value = re.sub(r'[ \u00a0\u202f]', ',', original[0])
        replacements.append((translated.start(), translated.end(), value))
    for start, end, value in reversed(replacements):
        target = target[:start] + value + target[end:]
    return target


def human(text):
    text = normalize(text)
    if re.match(r'^<[/!?]?[A-Za-z]', text):
        return False
    if re.match(r'^(?:var\(--|data-[\w-]+$|[\w.#-]+\[(?:name|placeholder|type|class|data-))', text):
        return False
    if re.search(r'[=×÷→]', text) and not (SPANISH.search(text) or SPANISH_EXTRA.search(text)):
        return False  # Formulae and technical identifiers are language-independent.
    if re.fullmatch(r'[^\s@]+@[^\s@]+\.[A-Za-z]{2,}', text) or re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}', text):
        return False
    if re.match(r'^(?:https?:|mailto:|tel:|data:|javascript:|#[\w-])', text) or re.fullmatch(r'\$\{.*\}[:;,.]?', text):
        return False
    if re.search(r'''\b(?:class|style|href|src|alt|data-[\w-]+|aria-[\w-]+)\s*=\s*["']''', text):
        return False
    if re.search(r'(?:\bfunction\s*\(|\bvar \w+\s*=|=>|\.querySelector\(|\}\s*catch\s*\()', text):
        return False
    return bool(re.search(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', text)) and not bool(
        re.fullmatch(r'(?:https?://|mailto:|tel:|#[\w-]|[\w./-]+\.(?:html|js|css|json|csv|png|jpg|webp|svg|pdf)).*', text))


class Translator:
    def __init__(self, cache, collect=False, allow_missing=False):
        self.cache, self.collect, self.allow_missing = cache, collect, allow_missing
        self.strings = set()
        self.dynamic = set()

    def __call__(self, text, escape=False):
        key = normalize(text)
        if not key or not human(key):
            return text
        self.strings.add(key)
        if self.collect:
            return text
        if key not in self.cache:
            if self.allow_missing:
                return text
            raise ValueError('Missing translation: ' + key[:160])
        result = self.cache[key]
        result = preserve_numbers(key, result)
        emails = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', key)
        target_emails = list(re.finditer(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+(?:[ ]\d+)?\.[A-Za-z]{2,}', result))
        if emails and len(emails) == len(target_emails):
            for original, translated in reversed(list(zip(emails, target_emails))):
                result = result[:translated.start()] + original + result[translated.end():]
        if re.search(r'\bceldas?\b', key, re.I) and not re.search(r'\b(?:solar|fotovolta)', key, re.I):
            result = re.sub(r'\b(?:cells|cell)\b', 'switchgear', result, flags=re.I)
        if re.search(r'\bvariadores?\b', key, re.I):
            result = re.sub(r'\bvariators\b', 'variable frequency drives', result, flags=re.I)
            result = re.sub(r'\bvariator\b', 'variable frequency drive', result, flags=re.I)
        if re.search(r'\b(?:BT|MT|AT|CCM)\b', key):
            for original, english in [('BT', 'LV'), ('MT', 'MV'), ('AT', 'HV'), ('CCM', 'MCC')]:
                if original == 'AT' and re.search(r'series?\s+AT', key, re.I):
                    continue
                result = re.sub(r'\b' + original + r'\b', english, result)
        if re.search(r'puesta a tierra|sistema(?:s)? de tierras?', key, re.I):
            result = re.sub(r'\b(?:landing|landings|lands)\b', 'grounding', result, flags=re.I)
        if re.search(r'\btableros?\b', key, re.I):
            result = re.sub(r'\bdashboards\b', 'switchboards', result, flags=re.I)
            result = re.sub(r'\bdashboard\b', 'switchboard', result, flags=re.I)
        if re.search(r'\balimentaci[oó]n\b', key, re.I):
            result = re.sub(r'\bfood\b', 'power supply', result, flags=re.I)
        if re.search(r'tensi[oó]n', key, re.I) and not re.search(r'mec[aá]nic|jalado|tracci[oó]n|tendido', key, re.I):
            result = re.sub(r'\btension\b', 'voltage', result, flags=re.I)
        # Keep the original whitespace around inline elements.
        prefix = re.match(r'^\s*', text).group()
        suffix = re.search(r'\s*$', text).group()
        return prefix + (html.escape(result, quote=True) if escape else result) + suffix


def english_name(name):
    return ALIASES.get(name, name)


def route(value, asset=False):
    """Resolve root Spanish resources relative to their English counterpart."""
    if not value or value.startswith(('#', 'data:', 'mailto:', 'tel:', 'javascript:', '//')):
        return value
    if not re.match(r'^(?:https?://(?:www\.)?vmsenergy\.com/|/?(?:assets/|en/)|/?[\w.-]+\.(?:html|js|css|json|csv)(?:[?#]|$))', value):
        return value
    value = value.replace('assets/img/landings/fv/cable-fotovoltaico-kei-fv.webp',
                          'assets/img/landings/fv/cable-fotovoltaico-kei-fv-rojo.webp')
    parts = urlsplit(html.unescape(value))
    if parts.netloc and parts.netloc not in {'vmsenergy.com', 'www.vmsenergy.com'}:
        return value
    path = parts.path
    if path.startswith('/en/'):
        return value
    clean = path.lstrip('./')
    if clean in NAMES or path in {'/', ''} and parts.netloc:
        target = english_name(clean or 'index.html')
        return urlunsplit(('', '', target, parts.query, parts.fragment))
    if parts.netloc:
        return value
    if path.startswith('/'):
        return value
    if (clean.startswith('assets/data/') and Path(clean).suffix.lower() in {'.json', '.csv'}) or clean == 'vacantes.json':
        return value  # Localized data is mirrored under en/.
    if clean in {'vms-header.js', 'vms-formularios.js', 'vms-medicion.js'}:
        return value
    if clean.startswith('assets/'):
        return '../' + value
    if clean and (ROOT / clean).exists():
        return '../' + value
    return value


def css_urls(text):
    return re.sub(r'''url\(\s*(["']?)([^)'"\s]+)\1\s*\)''',
                  lambda m: 'url(' + m[1] + route(m[2], True) + m[1] + ')', text)


def js_human(value):
    if not human(value) or not SPANISH.search(value):
        return False
    # Selectors, code, IDs and API values must retain their original meaning.
    if re.search(r'(?:^[.#\[]|^[\w-]+\s*[:=]\s*|=>|\bfunction\b|\breturn\b|\.(?:html|json|csv|css|js|pdf|webp|png|jpg)\b)', value):
        return False
    if re.fullmatch(r'[a-z]+(?:[-_][a-z0-9]+)+', value):
        return False
    return ' ' in value or bool(re.search(r'[áéíóúñ¿¡]', value))


def javascript(text, tr):
    if esprima is None:
        raise RuntimeError('Install scripts/requirements-english.txt to rebuild translated pages.')
    edits = []
    tokens = esprima.tokenize(text, {'range': True, 'tolerant': True})
    if tokens.errors:
        raise ValueError('Cannot tokenize JavaScript: ' + str(tokens.errors))
    for token in tokens:
        if token.type in {'String', 'Template'}:
            start, end = token.range
            raw = text[start:end]
            if token.type == 'Template':
                # Template tokens include the opening ` or } and closing ` or ${.
                value = raw[1:-2] if raw.endswith('${') else raw[1:-1]
                quote = '`'
            else:
                quote, value = raw[0], raw[1:-1]
            if token.type == 'String' and value in {'es-MX', 'es-ES', 'es_MX'}:
                edits.append((start, end, quote + 'en-US' + quote))
                continue
            # Retain data and filter identifiers. The local runtime translates DOM output.
            # Parse literals through the JS AST so quotes inside regexes never become copy.
            chunks = [unescape_js(value).replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
                      .replace('\\' + quote, quote)]
            selector = bool(re.search(r'\.(?:querySelector(?:All)?|getElementById|matches|closest)\s*\(\s*$', text[max(0, start-100):start]))
            for chunk in chunks:
                if selector:
                    continue
                if '<' in chunk and re.search(r'<[a-zA-Z/!]', chunk):
                    chunk = re.sub(r'^(?:["\'][^<>]*?>|>)', '', chunk)
                    fragment_tr = Translator(tr.cache, collect=tr.collect, allow_missing=tr.allow_missing)
                    transform_html(chunk, fragment_tr, fragment=True)
                    tr.strings.update(fragment_tr.strings)
                    tr.dynamic.update(fragment_tr.strings)
                elif spanish_copy(chunk) and not re.search(r'^[.#\[]|\$\{|\\[wdsb]', chunk):
                    tr(chunk)
                    tr.dynamic.add(normalize(chunk))
            # Routes are always rewritten, including shared assets and fetch() resources.
            routed = route(value)
            if routed != value:
                replacement = raw[:1] + routed + ('${' if raw.endswith('${') else raw[-1:])
                edits.append((start, end, replacement))
            else:
                # Messages displayed outside the DOM cannot be observed by the localizer.
                before = text[max(0, start-100):start]
                if token.type == 'String' and re.search(r'\b(?:Error|alert|confirm|prompt|setCustomValidity)\s*\(\s*$', before) and human(value):
                    translated = tr(unescape_js(value))
                    if translated != value:
                        edits.append((start, end, json.dumps(translated, ensure_ascii=False)))
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def json_data(value, tr, key='', display=False):
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if spanish_copy(k):
                tr(k)
                tr.dynamic.add(normalize(k))
            result[k] = json_data(v, tr, k, display)
        return result
    if isinstance(value, list):
        return [json_data(v, tr, key, display) for v in value]
    if isinstance(value, str):
        if key == 'inLanguage' and display:
            return 'en'
        if display and key not in {'@type', '@context', 'url', 'logo', 'image', '@id', 'sameAs', 'item', 'contentUrl', 'embedUrl'} and human(value):
            return tr(value)
        if display and value.startswith(ORIGIN):
            path = urlsplit(value).path.lstrip('/') or 'index.html'
            if path in NAMES:
                parts = urlsplit(value)
                return ORIGIN + 'en/' + english_name(path) + ('#' + parts.fragment if parts.fragment else '')
            return value
        if key in MACHINE_KEYS or key.lower().endswith(('url', 'src', '_id', '_url', '_path', '_image')):
            if spanish_copy(value):
                tr(value)
                tr.dynamic.add(normalize(value))
            return route(value)
        if spanish_copy(value):
            translated = tr(value)
            tr.dynamic.add(normalize(value))
            # Structured data is translated; runtime data preserves filter/API contracts.
            return value
    return value


class Document(HTMLParser):
    def __init__(self, text, tr, fragment=False):
        super().__init__(convert_charrefs=False)
        self.text, self.tr, self.fragment = text, tr, fragment
        self.lines = [0] + [m.end() for m in re.finditer('\n', text)]
        self.edits, self.mode, self.script_type = [], None, ''
        self.text_start = self.text_end = None

    def pos(self):
        line, col = self.getpos()
        return self.lines[line-1] + col

    def flush(self):
        if self.text_start is None:
            return
        start, end = self.text_start, self.text_end
        raw = self.text[start:end]
        if self.mode == 'script':
            if 'ld+json' in self.script_type:
                try:
                    value = json.dumps(json_data(json.loads(raw), self.tr, display=True), ensure_ascii=False)
                except json.JSONDecodeError:
                    value = raw
            elif 'json' in self.script_type:
                try:
                    value = json.dumps(json_data(json.loads(raw), self.tr), ensure_ascii=False)
                except json.JSONDecodeError:
                    value = raw
            else:
                value = javascript(raw, self.tr)
        elif self.mode == 'style':
            value = css_urls(raw)
        else:
            value = self.tr(raw, escape=True)
        self.edits.append((start, end, value))
        self.text_start = self.text_end = None

    def handle_starttag(self, tag, attrs):
        self.flush()
        raw, start = self.get_starttag_text(), self.pos()
        attrdict = dict(attrs)
        def replace(m):
            name, quote, value = m['name'].lower(), m['q'], m['value']
            if name == 'lang' and tag == 'html':
                value = 'en'
            elif name in ATTRS or (name == 'content' and tag == 'meta' and
                    (attrdict.get('name') in META or attrdict.get('property') in META)):
                value = self.tr(value, escape=True)
            elif name == 'value' and tag == 'input' and attrdict.get('type') in {'submit', 'button', 'reset'}:
                value = self.tr(value, escape=True)
            elif name == 'content' and tag == 'meta' and attrdict.get('property') == 'og:locale':
                value = 'en_US'
            elif name == 'content' and tag == 'meta' and attrdict.get('property') == 'og:url':
                value = ORIGIN + 'en/' + english_name(Path(urlsplit(value).path).name)
            elif name in URL_ATTRS:
                if name == 'href' and tag == 'link' and attrdict.get('rel') in {'canonical', 'alternate'}:
                    return m[0]
                value = route(value)
            elif name == 'style':
                value = css_urls(value)
            elif name.startswith('on'):
                value = html.escape(javascript(html.unescape(value), self.tr), quote=True)
            elif name == 'srcset':
                value = re.sub(r'(^|,\s*)(\S+)', lambda s: s[1]+route(s[2]), value)
            return m['name'] + '=' + quote + value + quote
        raw = ATTR_RE.sub(replace, raw)
        self.edits.append((start, start + len(self.get_starttag_text()), raw))
        if tag in {'script', 'style'}:
            self.mode = tag
            self.script_type = attrdict.get('type', '')
        # Option.value otherwise changes when its translated label changes.
        if tag == 'option' and 'value' not in attrdict:
            end = self.text.find('</option', start)
            if end != -1:
                label = html.unescape(re.sub('<[^>]*>', '', self.text[start+len(self.get_starttag_text()):end])).strip()
                previous = self.edits.pop()
                self.edits.append((previous[0], previous[1], previous[2][:-1] + ' value="' + html.escape(label, quote=True) + '">'))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.flush()
        if tag in {'script', 'style'}:
            self.mode = None

    def handle_data(self, data):
        start = self.pos()
        if self.text_start is None:
            self.text_start = start
        self.text_end = start + len(data)

    def handle_entityref(self, name):
        start = self.pos()
        if self.text_start is None:
            self.text_start = start
        self.text_end = start + len(name) + 1 + int(self.text[start+len(name)+1:start+len(name)+2] == ';')

    def handle_charref(self, name):
        start = self.pos()
        if self.text_start is None:
            self.text_start = start
        self.text_end = start + len(name) + 2 + int(self.text[start+len(name)+2:start+len(name)+3] == ';')

    def handle_comment(self, data):
        self.flush()

    def result(self):
        self.flush()
        result = self.text
        for start, end, value in sorted(self.edits, reverse=True):
            result = result[:start] + value + result[end:]
        return result


def transform_html(text, tr, fragment=False):
    doc = Document(text, tr, fragment)
    doc.feed(text)
    doc.close()
    return doc.result()


def sources(tr, write=False):
    for path in PAGES:
        text = path.read_text(encoding='utf-8-sig')
        text = re.sub(r'\s*<script\b[^>]*src=["\'](?:\.\./)?vms-language\.js["\'][^>]*>\s*</script>', '', text, flags=re.I)
        # hreflang/canonical are rebuilt consistently below, including reciprocal pairs.
        text = re.sub(r'\s*<link\b[^>]*\brel=["\'](?:alternate|canonical)["\'][^>]*>', '', text, flags=re.I)
        result = transform_html(text, tr)
        if path.name == 'carreras.html':
            result = result.replace("['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic']",
                                    "['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']")
        name = english_name(path.name)
        links = ('\n  <link rel="canonical" href="' + ORIGIN + 'en/' + name + '" />' +
                 '\n  <link rel="alternate" hreflang="es-MX" href="' + ORIGIN + path.name + '" />' +
                 '\n  <link rel="alternate" hreflang="en" href="' + ORIGIN + 'en/' + name + '" />' +
                 '\n  <link rel="alternate" hreflang="x-default" href="' + ORIGIN + path.name + '" />\n')
        runtime = '\n<script src="translations.js"></script>\n<script src="i18n.js"></script>\n<script src="../vms-language.js" defer></script>\n'
        result = re.sub(r'</head\s*>', links + runtime + '</head>', result, count=1, flags=re.I)
        if write:
            out = ROOT / 'en' / name
            out.parent.mkdir(parents=True, exist_ok=True)
            write_utf8(out, result)
            if name != path.name:
                write_utf8(ROOT / 'en' / path.name, result)
    for name in ('vms-header.js', 'vms-formularios.js', 'vms-medicion.js'):
        result = javascript((ROOT / name).read_text(encoding='utf-8-sig'), tr)
        if name == 'vms-formularios.js':
            result = result.replace("appearance: 'interaction-only',", "appearance: 'interaction-only',\n            language: 'en',")
        if write:
            write_utf8(ROOT / 'en' / name, result)
    for path in [ROOT / 'vacantes.json', *sorted((ROOT / 'assets/data').glob('*.json'))]:
        data = json_data(json.loads(path.read_text(encoding='utf-8-sig')), tr)
        if write:
            out = ROOT / 'en' / path.relative_to(ROOT)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for path in sorted((ROOT / 'assets/data').glob('*.csv')):
        rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
        result = [rows[0]] if rows else []
        for row in rows[1:]:
            for v in row:
                if spanish_copy(v):
                    tr(v)
                    tr.dynamic.add(normalize(v))
            result.append([route(v) for v in row])
        if write:
            out = ROOT / 'en' / path.relative_to(ROOT)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open('w', encoding='utf-8', newline='') as stream:
                csv.writer(stream).writerows(result)
    if write:
        # Exact messages plus phrase matching for strings assembled by calculators/catalogs.
        overrides = json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8'))
        dictionary = {key: tr(key) for key in sorted(tr.strings | set(overrides))
                      if key in tr.cache}
        dictionary = {key: value for key, value in dictionary.items() if key != value}
        (ROOT / 'en/translations.js').write_text('window.VMS_EN_TRANSLATIONS = ' +
            json.dumps(dictionary, ensure_ascii=False, separators=(',', ':')) + ';\nwindow.VMS_EN_PAGES = ' +
            json.dumps({name: english_name(name) for name in sorted(NAMES)}, ensure_ascii=False) + ';\n', encoding='utf-8')
        (ROOT / 'en/i18n.js').write_text((ROOT / 'scripts/english-runtime.js').read_text(encoding='utf-8'), encoding='utf-8')


def connect_spanish():
    """Minimal changes to the native pages: language switch and reciprocal SEO links."""
    for path in PAGES:
        if path.name in {'catalogo-proyectos.html', 'proyectos-vms.html'}:
            continue  # Staging-owned native pages use the shared header's language loader.
        text = path.read_text(encoding='utf-8-sig')
        text = re.sub(r'\s*<link\b(?=[^>]*\brel=["\']alternate["\'])(?=[^>]*\bhreflang=["\']en["\'])[^>]*>', '', text, flags=re.I)
        addition = '\n  <link rel="alternate" hreflang="en" href="' + ORIGIN + 'en/' + english_name(path.name) + '" />\n'
        if 'src="vms-language.js"' not in text:
            addition += '  <script src="vms-language.js" defer></script>\n'
        text = re.sub(r'</head\s*>', addition + '</head>', text, count=1, flags=re.I)
        write_utf8(path, text)


def english_sitemap():
    path = ROOT / 'sitemap.xml'
    text = path.read_text(encoding='utf-8')
    # Keep the existing Spanish sitemap entries and their editorial dates untouched.
    entries = re.findall(r'<url>.*?</url>', text, re.S)
    existing = set(re.findall(r'<loc>(.*?)</loc>', text))
    extra = []
    for entry in entries:
        match = re.search(r'<loc>' + re.escape(ORIGIN) + r'([^<]*)</loc>', entry)
        if not match or match[1].startswith('en/'):
            continue
        name = match[1] or 'index.html'
        if name not in NAMES:
            continue
        url = ORIGIN + 'en/' + english_name(name)
        if url in existing:
            continue
        extra.append(re.sub(r'<loc>.*?</loc>', '<loc>' + url + '</loc>', entry, count=1))
    if extra:
        text = text.replace('</urlset>', '  ' + '\n  '.join(extra) + '\n</urlset>')
        write_utf8(path, text)


def translate_batch(strings):
    # Numbered lines give a stable alignment; fall back to individual requests if needed.
    joined = '\n'.join(f'[{i}] {s}' for i, s in enumerate(strings))
    def request(text):
        url = 'https://translate.googleapis.com/translate_a/single?' + urlencode({
            'client': 'gtx', 'sl': 'es', 'tl': 'en', 'dt': 't', 'q': text})
        for attempt in range(5):
            try:
                with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=45) as response:
                    data = json.load(response)
                return ''.join(part[0] or '' for part in data[0])
            except HTTPError as exc:
                if exc.code == 429:
                    raise RuntimeError('Translation rate limit reached; cache saved. Retry later at a lower rate.') from exc
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
    if len(joined) > 4000:
        return {s: ' '.join(request(s[i:i+2500]).strip() for i in range(0, len(s), 2500)) for s in strings}
    translated = request(joined)
    parts = re.split(r'\[\s*(\d+)\s*\]\s*', translated)
    found = {int(parts[i]): parts[i+1].strip() for i in range(1, len(parts)-1, 2)}
    if set(found) != set(range(len(strings))):
        return {s: request(s).strip() for s in strings}
    return {s: found[i] for i, s in enumerate(strings)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--translate', action='store_true')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--connect', action='store_true')
    parser.add_argument('--delay', type=float, default=2.0)
    parser.add_argument('--reuse', action='store_true', help='Reuse translations differing only in case or decorative prefixes')
    parser.add_argument('--allow-missing', action='store_true', help='Authoring preview only; final builds must omit this flag')
    parser.add_argument('--prune', action='store_true', help='Keep only currently used translations and editorial overrides')
    args = parser.parse_args()
    if args.connect:
        connect_spanish()
    cache = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.exists() else {}
    cache.update(json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8')))
    for key, value in list(cache.items()):
        decoded = unescape_js(key)
        if decoded != key:
            cache.setdefault(decoded, unescape_js(value))
    tr = Translator(cache, collect=True)
    sources(tr)
    missing = sorted(tr.strings - cache.keys())
    print(f'{len(PAGES)} pages; {len(tr.strings)} unique strings; {len(missing)} missing; {sum(map(len, missing)):,} characters', flush=True)
    if args.prune:
        editorial = json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8'))
        cache = {key: value for key, value in cache.items() if key in tr.strings or key in editorial}
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if args.collect:
        (ROOT / 'scripts' / 'english-pending.json').write_text(json.dumps(missing, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.reuse:
        def comparable(value):
            return re.sub(r'\s+', ' ', value.casefold().replace('—', '-').replace('–', '-')).strip(' .:;›»→✓✔✗•·\t\n')
        known = {}
        for key, value in cache.items():
            known.setdefault(comparable(key), value)
        added = 0
        for key in missing:
            match = known.get(comparable(key))
            if match is not None:
                prefix = re.match(r'^[^\w¿¡]+', key)
                cache[key] = (prefix[0] if prefix else '') + match
                added += 1
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(f'Reused {added} translations without a network request.', flush=True)
    if args.translate and missing:
        batches, batch, size = [], [], 0
        for item in missing:
            if batch and size + len(item) > 3000:
                batches.append(batch)
                batch, size = [], 0
            batch.append(item)
            size += len(item) + 12
        if batch:
            batches.append(batch)
        # Deliberately serialize the public authoring service and respect rate limits.
        for count, batch in enumerate(batches, 1):
            cache.update(translate_batch(batch))
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            if count % 10 == 0 or count == len(batches):
                print(f'Translated {count}/{len(batches)} batches', flush=True)
            time.sleep(args.delay)
    if args.build:
        cache.update(json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8')))
        sources(Translator(cache, allow_missing=args.allow_missing), write=True)
        english_sitemap()
        print('English mirror built offline.', flush=True)


if __name__ == '__main__':
    main()
