# Versiones en español e inglés

El español sigue siendo la versión nativa en la raíz. Cada página publicada tiene
su contraparte estática en `en/`; el botón EN/ES conserva la página, los parámetros
y la sección actuales. Se conservan las rutas inglesas existentes
`oil-gas-solutions.html` y `oil-gas-maintenance.html`, además de sus alias con el
nombre del archivo español.

`vms-language.js` controla el cambio de idioma. El encabezado compartido también
lo carga en las dos páginas nativas de proyectos, que el repositorio reserva al
flujo de staging. Esos dos HTML originales no se modifican.

## Regenerar las páginas

Desde la raíz, con Python 3.8 o posterior:

```powershell
python -m pip install -r scripts/requirements-english.txt
python scripts/build_english.py --build
python scripts/audit_english.py
python -m unittest discover -s scripts -p test_english.py
python scripts/check_english_authoring.py
```

La generación final es local y falla si falta alguna traducción. No usa una API de
traducción durante el build ni durante las visitas. `--allow-missing` sirve solo
para vistas previas durante la edición; no debe usarse para la entrega final.

La memoria inicial, asistida por Google Translate y un modelo Marian ejecutado
localmente, vive en `scripts/english-translations.json`. Las correcciones
editoriales y el vocabulario técnico se mantienen en
`scripts/english-overrides.json`; sus valores tienen prioridad al regenerar.

Después de cambiar contenido español, `--collect` guarda los textos pendientes
en `scripts/english-pending.json`. Este archivo es temporal. Traducir los nuevos
textos y añadirlos a la memoria antes de volver a generar. La opción explícita
`--translate` envía los textos pendientes a Google y requiere autorización para
ese envío; respeta el límite de solicitudes y permite reanudar con la memoria
ya guardada. `translate_english_native.py --run` es una alternativa local opcional
con ONNX Runtime y los pesos públicos de `Xenova/opus-mt-es-en`.
Ese backend opcional requiere `onnxruntime==1.17.3`, `tokenizers==0.20.3` y
`numpy==1.24.4` (Python 3.8–3.11). Divide párrafos en oraciones y protege los
nombres comerciales durante la traducción. Los pesos se guardan en el directorio
temporal y no forman parte del sitio publicado.

## Contenido dinámico

Los archivos ingleses `translations.js` e `i18n.js` traducen el texto que producen
catálogos, filtros, vacantes, calculadoras y cookies. Las claves de datos, nombres
de campos, identificadores, valores de opciones y contratos del backend se
conservan. Los datos de contacto y las cifras técnicas están protegidos. La
versión inglesa utiliza sus copias de los scripts de formularios y medición;
los recursos visuales y documentos originales se comparten con la versión nativa.

Los títulos, descripciones, datos estructurados, enlaces de idioma y sitemap se
generan con las rutas inglesas. Las imágenes y documentos oficiales descargables
no se modifican.

## Comprobar en navegador

`check_english_browser.py` usa un Edge/Chrome de pruebas con depuración local en
el puerto 9224 y un servidor HTTP en `127.0.0.1:8765`. Requiere
`websocket-client`. `--all` revisa las páginas y sus alias; `--screenshots` captura
la portada en escritorio y móvil. Los reportes y capturas se guardan en el
directorio temporal del sistema. Estas comprobaciones no envían formularios.
`check_english_interactions.py` comprueba además cookies, filtros, calculadora,
menú móvil y el cambio de idioma en las páginas de proyectos.
