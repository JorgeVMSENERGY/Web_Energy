# Handoff — Correcciones SEO / AEO (12-nov-2026)

Rama: `hugo` · Cambios sin commit (revisar con `git diff`). ~105 archivos HTML + `sitemap.xml`.
Checklist completo: https://claude.ai/artifact/RKjT88wui9D4mNbDKPkDm2

## Qué cambió

| # | Cambio | Archivos |
|---|---|---|
| 1 | Schema Organization unificado: un bloque completo por página con `@id` https://vmsenergy.com/#organization (razón social "VMS Energy de México, S. de R.L. de C.V.", 2 direcciones, teléfonos, correo, redes, ISO); provider/publisher/author ahora referencian ese `@id` | 104 páginas |
| 2 | FAQ visible agregado (sección `.vfaq`, CSS embebido, sin JS) con las mismas preguntas que ya tenía el JSON-LD | lp-turbogeneradores-woodward, lp-tendido-cableado-electrico-industrial, lp-mantenimiento-oil-gas, soluciones-ccm-industrial, en/turbine-generator-controls, index, en/index |
| 3 | Quitado el FAQPage genérico ("¿Ofrecen integración multimarcas?") que no se mostraba | aviso-privacidad, codigo-conducta, directorio, buzon-sugerencias, politica-antisoborno, politica-interna_antilavado, nosotros, soluciones, proyectos-vms, blog, blog-variadores-industria-minera |
| 4 | Sectoriales: las 6 preguntas del schema se agregaron al FAQ visible ("¿Cómo iniciamos un proyecto?" al final); schema = FAQ visible | soluciones_automatizacion_control, _electromecanicas, _iluminacion_industrial, _infraestructura_energetica, _oil_gas, _solar_industrial, _verde_no_solar |
| 5 | Dominio viejo www.vms-energy.mx → vmsenergy.com; canonical a sí mismas y agregadas al sitemap (se posicionan por separado). especialidad_servicio sigue siendo redirección a soluciones | lp-cables-ehv, lp-kei-cable-potencia, lp-cables-fv, lp-esp-cables, especialidad_servicio, sitemap.xml |
| 6 | lp-cables-alta-tension agregada a sitemap.xml | sitemap.xml |
| 7 | JSON-LD FAQPage reescrito para que sea idéntico al FAQ visible | 26 páginas |
| 8 | JSON-LD FAQPage nuevo (antes de `</head>`) a partir del FAQ visible | 14 páginas |
| 9 | FAQ nuevo (visible + JSON-LD) generado del contenido de la página; Triol menciona inventario propio para entrega inmediata | lp-industria-40, lp-motores-electricos-industriales, lp-retrofit-subestaciones, lp-triol-variadores, servicios-infraestructura-acondicionamiento, calculadora-ahorro-bess-gdmth, triol-variador-detalle, en/about, en/oil-gas-maintenance, en/triol-drives |
| 13 | `<title>` ≤ 65 caracteres (y og:title / twitter:title cuando eran iguales) | 35 páginas |
| 14 | meta description ≤ 160 caracteres (y og/twitter cuando eran iguales) | 55 páginas |
| 15 | blog.html: `@type` Article → Blog, sin fecha placeholder; lastmod del sitemap con fecha real | blog.html, sitemap.xml |

## Verificación hecha
- 0 errores de JSON-LD (se parsean todos los bloques).
- Etiquetas balanceadas igual que en HEAD (section, details, div, script, style, main).
- Render revisado en lp-turbogeneradores-woodward y soluciones_oil_gas.
- Se recomienda validar 2–3 URLs en https://search.google.com/test/rich-results después de publicar.

## Pendiente

- Puntos 10, 11, 12, 16: ver checklist.
