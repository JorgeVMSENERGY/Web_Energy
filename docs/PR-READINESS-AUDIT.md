# Auditoría C1 → C2A → PR offline validation

Fecha: 11 de noviembre de 2026. Rama: `feat/optimize-aws-web-deploy`.
Resultado: preparado para revisión y prueba como Draft PR; **no preparado para
merge bajo la condición de no ejecutar producción**. No se creó ningún PR.

## Base comprobada y payload web

`git ls-remote origin refs/heads/main refs/heads/staging` se ejecutó en modo
lectura. No se hizo fetch, checkout, commit, push ni merge en este proyecto.

| Referencia | SHA observado |
| --- | --- |
| HEAD y origin/main, coincidentes con main remoto | `72ad37efaf16c796649c458c273e70984061fdb5` |
| main local, atrasada | `1276e23ff9e241b16e1527ee959faf2d2ab6323a` |
| staging remoto y origin/staging | `689d4068dfe96c62e65c6faaa5fcd4f12cbbd521` |

No hay modificaciones versionadas respecto de HEAD, ni cambios en el índice.
Los 34 archivos C1–CI encontrados inicialmente eran untracked. No forman parte
del PR ni de GitHub hasta que una persona los incluya explícitamente en commits.
Esta auditoría agrega script, informe, manifiesto y evidencia JSON.

El [reporte por ruta y SHA256](../scripts/pr-readiness-audit.json) contiene el
inventario completo de ambas bases y todas las diferencias. Se aplica la política
publicable de `deploy_plan.py`, equivalente a las exclusiones del deploy actual.
Se hashean blobs Git, no archivos convertidos a CRLF por el checkout. Los cambios
locales o untracked publicables se incorporan hipotéticamente por sus bytes.

| Comparación del payload potencial | Resultado |
| --- | --- |
| Contra main remoto verificado | 947 rutas en ambos lados; 0 altas, bajas o cambios SHA256 |
| Contra main local atrasada | 92 rutas con diferencias históricas; no son cambios nuevos del PR |
| Nuevos archivos seleccionados | Todos excluidos del payload: scripts/, .github/ o Markdown |

No hay incorporación ni modificación nueva de las páginas protegidas respecto
de main remoto. Sus SHA256 de blobs en HEAD y main remoto son idénticos:

| Ruta | Bytes | SHA256 |
| --- | ---: | --- |
| catalogo-proyectos.html | 111000 | `5ca9bb3a92de2698f4569acd251543837e94e13cb96002894fdd4a5558cfa9c8` |
| proyectos-vms.html | 192796 | `5b8cbf4f244f21717b5b6a92e1289259369c5ac7e397b7c293d3b41550dd264e` |

Sí difieren frente a main local atrasada; el JSON conserva también esos hashes.
La comparación es Git contra Git: no demuestra el estado de S3 ni CloudFront.
Repetir la verificación de la base si main avanza antes de abrir el PR.

## Lista exacta para la futura promoción

Usar [scripts/PR-INCLUDE.txt](../scripts/PR-INCLUDE.txt): **29 rutas explícitas**,
incluido el propio manifiesto. Es una lista para revisión humana, no un comando
que se haya ejecutado. No usar `git add .` ni asumir que GitHub recibirá untracked.

Para el job son indispensables estos **19 archivos nuevos**:

```text
.github/workflows/validate-pr.yml
scripts/audit_performance.py
scripts/audit_s3.py
scripts/audit_s3_boto3.py
scripts/c1_plan.py
scripts/c2a_observe.py
scripts/deploy_plan.py
scripts/git_batch.py
scripts/run_pr_offline.py
scripts/validate_pr_diff.py
scripts/test_audit_performance.py
scripts/test_audit_s3.py
scripts/test_audit_s3_boto3.py
scripts/test_aws_cli_encoding.py
scripts/test_c1_plan.py
scripts/test_c2a_observe.py
scripts/test_deploy_plan.py
scripts/test_git_batch.py
scripts/test_pr_validation.py
```

El resto de las 29 rutas conserva documentación, CLI de simulación C1, requisitos
del auditor y herramientas/evidencia explicativa de esta auditoría. Además se
requieren dos archivos **ya versionados en main remoto**, sin modificarlos:
`infra/forms/tests/test_turnstile.py` y `infra/forms/lambda/receiver/app.py`.
El segundo se carga por ruta desde la prueba; no basta copiar solo el test.

No se requieren los siguientes ocho JSON históricos para ejecutar CI. Dejarlos
fuera del PR de validación reduce datos operativos y ruido de revisión; no fueron
borrados. Están inventariados, hasheados y analizados como JSON en el reporte:

```text
scripts/c1_simulations/baseline-auditado.json
scripts/c1_simulations/casos-sinteticos.json
scripts/c1_simulations/fallo-recuperacion-simulados.json
scripts/c1_simulations/noop-auditado.json
scripts/c1_simulations/observacion-auditada.json
scripts/c2a_simulations/revisar-escritores-no-ejecutado.json
scripts/c2a_simulations/simulacion-947-versiones.json
scripts/deploy-metadata-report.json
```

Los documentos históricos pueden enlazar esos JSON. Si se exige conservar sus
enlaces dentro de GitHub, revisar y agregar explícitamente los ocho como evidencia
histórica; no confundirlos con dependencias de ejecución ni con estado AWS actual.

El reporte generado `scripts/pr-readiness-audit.json` es evidencia local opcional,
no dependencia del job y no está en la lista de 29. Si se conserva en el PR,
revisar su contenido operativo y agregar esa ruta de forma explícita: serían 30.
El reporte excluye su propio hash y el del manifiesto generado para evitar
autorreferencias; los demás archivos tienen tamaño y SHA256.

## Auditoría de los archivos y dependencias

| Archivos o grupo | Revisión y dependencia |
| --- | --- |
| deploy_plan.py, git_batch.py | Lee blobs por OID con Git; streaming SHA256; sin cliente AWS. Necesita Git real, no descarga ZIP de checkout. |
| audit_performance.py | Instrumentación local con librería estándar; sin red ni credenciales. |
| audit_s3.py | CLI de lectura real solo con --read-s3; importarla no ejecuta el CLI. Allowlist rechaza escrituras antes del subprocess. CI solo llama fixtures/mocks/loopback. |
| audit_s3_boto3.py | Sesiones solo al construir el lector; imports diferidos. Lecturas con validación previa; tests inyectan sesiones y endpoints locales. |
| c1_plan.py, c1_simulator.py | Planes y diarios locales; sin apply real. applyBlocked=true y autorización no implementada. El simulador no lo invoca el job. |
| c2a_observe.py | CLI de lectura habilitada explícitamente; no la ejecuta CI. Reutiliza lector Boto3 y plan C1; pruebas con FakeReader y loopback. |
| Nueve test_*.py seleccionados | Fixtures propios; no necesitan los ocho JSON históricos, reportes TEMP del autor ni sesión SSO. Git crea commits únicamente en repositorios temporales de fixtures. |
| validate_pr_diff.py | Solo Git, JSON y diagnóstico. Usa base/head del evento, no presencia de archivos ni main local. |
| run_pr_offline.py | Exige suites conocidas; descubre scripts y formularios; CI falla con errores, skips o cero pruebas. No autentica integridad del código del PR. |
| validate-pr.yml | Solo pull_request→main, contents:read; sin ambiente productivo ni configuración AWS. Prepara herramientas antes de aislar pruebas. |
| Cuatro documentos históricos C1/C2A/auditor/plan | Describen fases y límites; sus comandos --read-s3 son instrucciones manuales, no pasos del job. No ejecutar esos ejemplos para probar el PR. |
| Dos documentos de CI y manifiesto | Revisión humana, limitaciones y procedimiento. No son inputs del plan ni autorización. |
| requirements-audit.txt | Solo Boto3 1.37.38. No basta por sí solo para test_pr_validation, que también necesita PyYAML. El workflow instala ambos explícitamente. |
| Ocho JSON históricos | JSON válido; revisar applyBlocked por ocurrencia en el reporte. No demuestran frescura ni autenticidad de datos AWS y no se usan como fixtures de CI. |
| audit_pr_readiness.py y su JSON | Auditoría Git/local reproducible; imports AST, tamaños, hashes y manifiestos. Sin red ni modificación del índice. |

Se ampliaron las advertencias sensibles para cubrir también la cadena de imports
del auditor/planificador y el receptor de formularios, no solamente los entrypoints.
No se modificó código C1/C2A ni archivos productivos durante esta revisión.

## Python y herramientas Linux

| Entorno | Requisitos comprobables |
| --- | --- |
| Host ubuntu-latest x86_64 | Bash, Python, Git, Docker cliente y daemon. El nuevo preflight imprime versiones, comprueba arquitectura, historial no shallow y existencia de base/head/merge-base. |
| Imagen python:3.12-slim | Python 3.12; apt instala git, curl y unzip. La construcción necesita acceso a registro de imágenes, Debian, PyPI y descarga pública de AWS CLI. |
| Paquetes Python | boto3==1.37.38, PyYAML==6.0.3; dependencias transitivas botocore, s3transfer, jmespath, python-dateutil y six. pip check y pip show dejan diagnóstico dentro del contenedor. |
| AWS CLI | 2.36.42 para la regresión del incidente; otra versión puede fallar el test. Solo --version y comandos redirigidos a loopback durante estas pruebas. |
| Pruebas | --network none, --cap-drop ALL, no-new-privileges, repositorio readonly; directorios temporales del contenedor escribibles. Solo loopback necesita estar disponible. |

Las imágenes y referencias ubuntu-latest/python:3.12-slim son móviles. No se
promete reproducibilidad por digest ni disponibilidad de servicios de descarga.
El workflow actual instala dependencias transitivas sin hashes. Un fallo de
descarga o de runner debe dejar el check fallido, no autorizar un merge.

El inventario oficial de [Ubuntu](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
incluye Python, Git y Docker; las versiones efectivas deben comprobarse en el
preflight del PR. Localmente hay Python 3.12.10, Git 2.55.0.windows.3, Boto3 1.37.38,
PyYAML 6.0.3 y AWS CLI 2.36.42. El python por defecto es 3.8: se usó explícitamente
el entorno temporal de Python 3.12, no ese ejecutable.

Se encontró WSL Linux con Python 3.12.8 y una interfaz docker emulada por Podman,
pero falló por permisos de su runroot (/run/user/1000). No se cambió configuración
del sistema, no se instaló Docker y **no se construyó ni ejecutó el contenedor**.
La prueba Linux/Actions completa sigue pendiente en el Draft PR.

## Diff del PR y checkout limpio

`actions/checkout@v6` conserva `fetch-depth: 0`, sin sparse checkout ni filtros,
y `persist-credentials: false`. El checkout usa el merge sintético del PR; el
diagnóstico recibe los SHA base y head desde el evento. `git diff base...head`
calcula lo introducido por el PR desde el ancestro común y no incorpora cambios
hechos únicamente en la base. `--no-renames -z` conserva ambas rutas y nombres
con espacios/Unicode sin parsear líneas. No hay filtros paths que omitan el job.

La [documentación de checkout](https://github.com/actions/checkout) confirma que
profundidad 0 obtiene todo el historial. Un historial shallow o un fallback ZIP
sin Git no servirían: ahora el preflight los rechaza antes de validar el diff.

Se agregó una prueba con historial divergente: base avanza cambiando una página
protegida, head solo cambia index.html, se crea un merge temporal y se clona con
historial completo en detached HEAD. El diagnóstico solo atribuye index.html al
PR. Se mantienen pruebas de presencia histórica, renombrado y eliminación de un
control establecido. Todos los commits de estos fixtures son fuera del proyecto.

La suite se ejecutó en un clon temporal del contenido versionado más **solo**
las rutas seleccionadas. No se copió ningún JSON histórico. No equivale a una
ejecución real de GitHub: las adiciones todavía no están en un commit y la prueba
local corre en Windows, sin aislamiento Linux de red. Sí comprueba que los
imports y fixtures no dependen de archivos untracked omitidos de la selección.

## AWS y posibilidad de omitir controles

Para el YAML y código revisados: no hay secretos referenciados, id-token:write,
configure-aws-credentials, rol asumido, ambiente production, paso de deploy,
upload/delete S3 real ni invalidación CloudFront. El contenedor no recibe perfiles
AWS del host, token de Actions, secretos, socket del motor ni variables AWS del
runner. Los tests usan credenciales ficticias y endpoints de loopback; formularios
usa mocks. Las pruebas negativas rechazan siete clases de operaciones de escritura
o asunción de rol antes de CLI/SDK y comprueban applyBlocked=true.

**Sí: un PR puede sustituir su propio workflow y producir un check exitoso con
el mismo nombre sin ejecutar las pruebas.** Basta conservar name: PR offline
validation y cambiar sus steps por un comando exitoso. No lo impide que el
validador use código de la base: el PR puede eliminar también ese paso. Agregar
un workflow con otro job del mismo nombre introduce ambigüedad adicional.

La protección local detecta modificaciones accidentales de controles establecidos
si se conserva el workflow. No es una frontera de seguridad contra PRs arbitrarios.
Los tests también pertenecen al PR y pueden ser modificados. En esta primera
incorporación no existe validador confiable en la base: el bootstrap es explícito.
No se puede garantizar "nunca AWS" para una versión maliciosamente reescrita del
workflow; la confirmación corresponde exactamente a los archivos auditados.

Mitigación propuesta, sin modificar reglas: aprobación DevOps independiente del
autor; CODEOWNERS para .github/, scripts/ y pruebas/receptor de formularios, con
protección de CODEOWNERS; descartar aprobaciones al cambiar código, aprobar el
último push y limitar bypass. Para protección técnica del propio workflow, usar
un workflow obligatorio gestionado fuera de la rama del PR mediante ruleset de
organización si el plan lo permite. No basta exigir el nombre de un status check
o seleccionar GitHub Actions como fuente. No se verificó ni cambió el ruleset
remoto actual ni políticas IAM/OIDC de AWS.

Ver [reglas de GitHub](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

## Riesgos de abrir y fusionar

Abrir un Draft PR sobre esta rama no activa el workflow productivo revisado,
porque solo responde a push→main o workflow_dispatch. Sí puede ejecutar CI y
consumir minutos, descargar dependencias y publicar logs. Draft no es un sandbox
ni desactiva Actions. En PRs de forks GitHub puede requerir aprobación adicional
de ejecución; no usar pull_request_target para eludirla. Antes de aprobar una
ejecución, revisar el diff entero del workflow y scripts, incluso si hay un check
verde con el nombre esperado. Los JSON históricos contienen datos operativos;
su revisión o publicación son independientes de la prueba del job.
En un PR del mismo repositorio, contents:read no impide que una versión alterada
del YAML referencie secretos del repositorio disponibles para ese evento o pida
otros permisos. No se comprobó la disponibilidad de secretos ni la confianza
OIDC de AWS; no asumir que el estado Draft limita esas capacidades.

**Fusionar a main dispara el deploy actual incluso con cero cambios de payload.**
deploy-production.yml no filtra rutas. Invoca deploy-aws-production.ps1 -Apply,
que ejecuta s3 sync --delete y luego crea una invalidación /* si el sync termina
bien. Puede publicar/borrar por diferencias contra S3 y ejecutar invalidación
aunque los hashes Git no cambien. Este CI no convierte el deploy en incremental
ni bloquea ese flujo. No se debe fusionar para probar la validación bajo la
restricción de no desplegar. No ejecutar el script productivo ni siquiera en modo
dry-run: ese modo también contacta AWS para calcular su sync.

## Procedimiento futuro para Draft PR, sin merge

1. Una persona autorizada revisa las 29 rutas del manifiesto y los hashes del
   reporte. Confirmar main remoto actualizado, ausencia de cambios web/protegidos
   y que no se incluyeron modificaciones productivas. Esta auditoría no autoriza
   ni ejecuta los commits/push necesarios para abrir el PR.
2. Cuando se autorice esa preparación, incluir solo las rutas explícitas y
   comprobar el diff contra el SHA actualizado de main remoto. Los ocho JSON no
   son necesarios; tampoco el reporte local. No usar toda la carpeta como selección.
3. Crear Draft PR desde feat/optimize-aws-web-deploy hacia main con título de
   validación CI y la indicación NO MERGE / NO DEPLOY. Desactivar auto-merge si
   estuviera habilitado. No pulsar workflow_dispatch del deploy productivo.
4. DevOps distinto del autor revisa los archivos exactos que se ejecutarán antes
   de aprobar la ejecución si GitHub lo solicita. Revisar bootstrap y dependencias.
5. Observar el job exacto PR offline validation: preflight Linux/Git/Docker,
   SHAs/merge-base, diagnóstico sin páginas protegidas, build exitoso, versiones
   correctas y suite completa sin skips bajo --network none. Una ejecución
   cancelada, fallida o parcial no equivale a validación.
6. Comprobar que no se creó ningún run productivo por ese PR. No hacer merge,
   no ejecutar CLI --read-s3, scripts de deploy ni comandos de infraestructura.
   Mantener el PR en Draft mientras se revisa evidencia; cerrarlo si solo era
   una prueba. Los casos negativos del diff ya se cubren offline y no necesitan
   introducir cambios reales a páginas productivas para esta primera prueba.
7. Cualquier promoción a main es otra decisión con riesgo de despliegue. El
   procedimiento de activación futura de Proteccion-main está en PR-VALIDATION.md;
   requiere control independiente antes de tratar el check como garantía.

## Validación local

Suite final completa en el clon temporal con selección explícita: **140 pruebas
OK en 62.961 segundos, cero omitidas**, en modo PR_OFFLINE_STRICT=1. Incluye
scripts, formularios y siete regresiones del control de PR. La primera ejecución
en ese clon, antes de agregar la regresión de historial divergente, pasó 139
pruebas con cero omisiones. No se reutilizó ese resultado como prueba de la
versión final.

`actionlint` 1.7.7 sobre el YAML final: salida 0, sin diagnósticos; se deshabilitó
solo ShellCheck externo con -shellcheck=. El parser PyYAML y los tests verificaron
eventos, permisos, checkout, ausencia de condiciones que omitan steps, nombre del
job y opciones de aislamiento. `git diff --check` pasó; índice y contenido
versionado del proyecto sin cambios. Los archivos nuevos siguen untracked.

El contenedor Linux y el evento real de Actions **no fueron ejecutados**. La
validación local no demuestra todavía que la descarga/instalación del AWS CLI,
Docker, permisos de montaje y todos los tests funcionen juntos en ubuntu-latest.
Los preflights y el Draft PR resolverán esos puntos sin fusionar a main.

No se invocó el workflow productivo ni ningún comando AWS real. No hubo cambios
IAM, Terraform, S3 o CloudFront, commits del proyecto, push, merge ni cambios de
reglas. Las consultas remotas se limitaron a Git en lectura y documentación.

Para reproducir inventario: `python -B scripts/audit_pr_readiness.py`. Es una
operación local que escribe solo sus dos reportes bajo scripts/, sin staging ni
red. La frescura del main remoto se verifica por separado en modo lectura.
