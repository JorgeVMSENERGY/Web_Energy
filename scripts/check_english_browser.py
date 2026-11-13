"""Browser QA through an existing local Chromium debugging endpoint.

Serve the repository on 127.0.0.1:8765 and start a dedicated headless Edge/Chrome
with --remote-debugging-port=9224. Requires websocket-client; never submits forms.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
import time
import re
from urllib.request import Request, urlopen

tools = Path(os.environ.get('TEMP', '/tmp')) / 'vms-translation-tools'
if tools.exists():
    sys.path.insert(0, str(tools))
import websocket


class Browser:
    def __init__(self):
        with urlopen(Request('http://localhost:9224/json/new?about:blank', method='PUT')) as response:
            target = json.load(response)
        self.target = target['id']
        self.socket = websocket.create_connection(target['webSocketDebuggerUrl'], suppress_origin=True, timeout=20)
        self.number = 0
        self.errors = []
        self.call('Page.enable')
        self.call('Runtime.enable')
        self.call('Network.enable')
        self.call('Network.setBlockedURLs', {'urls': ['*googletagmanager.com*', '*google-analytics.com*', '*facebook.com/tr*']})

    def call(self, method, params=None):
        self.number += 1
        number = self.number
        self.socket.send(json.dumps({'id': number, 'method': method, 'params': params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get('method') == 'Runtime.exceptionThrown':
                self.errors.append(message['params']['exceptionDetails'])
            if message.get('id') == number:
                if 'error' in message:
                    raise RuntimeError(message['error'])
                return message.get('result', {})

    def evaluate(self, code):
        result = self.call('Runtime.evaluate', {'expression': code, 'returnByValue': True, 'awaitPromise': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(result['exceptionDetails'])
        return result.get('result', {}).get('value')

    def navigate(self, path):
        self.errors = []
        self.call('Page.navigate', {'url': 'http://127.0.0.1:' + os.environ.get('VMS_QA_PORT', '8765') + '/' + path})
        time.sleep(.2)
        for _ in range(70):
            if self.evaluate('document.readyState') == 'complete':
                break
            time.sleep(.1)
        time.sleep(.3)

    def close(self):
        self.socket.close()
        urlopen('http://localhost:9224/json/close/' + self.target).close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spanish', action='store_true')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--screenshots', action='store_true')
    args = parser.parse_args()
    pages = ['index.html', 'nosotros.html', 'soluciones.html', 'contacto.html',
             'procura.html', 'VMShop.html', 'carreras.html', 'catalogo-proyectos.html',
             'proyectos-vms.html', 'calculadora-ahorro-bess-gdmth.html',
             'lp-cable-de-potencia-kei.html', 'aviso-privacidad.html',
             'blog-mantenimiento-reparacion-subestaciones-at-mt-bt.html']
    if args.all:
        pages = [p.name for p in (Path(__file__).resolve().parents[1] / 'en').glob('*.html')]
    browser = Browser()
    results = []
    try:
        if args.screenshots:
            for name, width, height in [('desktop', 1440, 1000), ('mobile', 390, 844)]:
                browser.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height,
                             'deviceScaleFactor': 1, 'mobile': name == 'mobile'})
                browser.navigate('en/index.html')
                if name == 'mobile':
                    browser.evaluate('document.getElementById("menuToggle").click()')
                image = browser.call('Page.captureScreenshot', {'format': 'png'})['data']
                output = Path(os.environ.get('TEMP', '/tmp')) / ('vms-english-' + name + '.png')
                output.write_bytes(base64.b64decode(image))
                print(str(output), flush=True)
            return
        for page in pages:
            browser.navigate(('' if args.spanish else 'en/') + page)
            snapshot = browser.evaluate('''JSON.stringify({
              title:document.title, lang:document.documentElement.lang,
              switch:document.querySelector('.lang-switch')?.getAttribute('href'),
              overflow:document.documentElement.scrollWidth > innerWidth + 2,
              text:document.body.innerText.slice(0,100000),
              brokenImages:[...document.images].filter(x=>x.complete&&!x.naturalWidth&&x.getAttribute('src')).map(x=>x.getAttribute('src')),
              spanishLinks:[...document.querySelectorAll('a[href]')].filter(a=>a.origin===location.origin&&/\\.html$/.test(a.pathname)&&!a.pathname.startsWith('/en/')&&!a.matches('.lang-switch')).map(a=>a.getAttribute('href'))
            })''')
            row = json.loads(snapshot)
            row['page'] = page
            row['errors'] = browser.errors[:]
            row['spanishCopy'] = [row['text'][max(0,m.start()-35):m.end()+65] for m in re.finditer(
                r'\b(?:Seleccione|Selecciona|Solicitar|Enviar|Cotizar|Nosotros|Ingresa|Cargando|Guardar|Somos|Trabajamos|Conoce|Cotización|Suministro)\b', row['text'], re.I)]
            if not args.all or row['errors'] or row['brokenImages'] or row['spanishLinks']:
                print(json.dumps({k: (len(v) if k in {'spanishLinks', 'spanishCopy'} else v) for k, v in row.items() if k != 'text'}, ensure_ascii=False), flush=True)
            elif len(results) % 10 == 0:
                print('Pages checked: ' + str(len(results)+1), flush=True)
            results.append(row)
        output = Path(os.environ.get('TEMP', '/tmp')) / 'vms-english-browser-results.json'
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Browser report: ' + str(output), flush=True)
        print(json.dumps({'checked': len(results), 'scriptErrors': sum(bool(r['errors']) for r in results),
                          'brokenImages': sum(bool(r['brokenImages']) for r in results),
                          'linksLeavingEnglish': sum(bool(r['spanishLinks']) for r in results)}, ensure_ascii=False), flush=True)
    finally:
        browser.close()


if __name__ == '__main__':
    main()
