# Validación offline de Pull Requests

Workflow: `.github/workflows/validate-pr.yml`.
Job ID: `validate-pr`. Nombre exacto del check: **PR offline validation**.
La auditoría posterior, selección exacta de archivos, comprobación de base remota
y resultados actualizados están en [PR-READINESS-AUDIT.md](PR-READINESS-AUDIT.md).
Evento: `pull_request` hacia `main`, sin filtros por archivos ni condiciones que
omitan el job. No se agregó `staging`: el workflow actual de despliegue solo
contempla `main`. Si se incorpora esa rama al flujo, revisar los destinos.

## Qué comprueba

- Ejecuta `scripts/test_*.py` y `infra/forms/tests/test_*.py` sobre el merge del PR.
  Exige las suites conocidas y falla ante cualquier prueba fallida u omitida en CI.
- C1 y C2A: NO-OP, cambios propuestos, drift, evidencia inválida o caducada,
  recuperación simulada, metadatos e invariantes `applyBlocked is True`.
- Pruebas negativas para put/delete/copy/sync, invalidaciones y assume-role:
  los lectores CLI y SDK deben rechazar antes de invocar el transporte.
- Compara `base.sha...head.sha`, usando `--no-renames -z`. Detecta ambos lados
  de renombrados, altas, modificaciones y bajas. Solo bloquea las páginas
  `catalogo-proyectos.html` y `proyectos-vms.html` cuando aparecen en ese diff.
- Registra SHAs y rutas en logs y en el resumen del job. Advierte sobre cambios
  en `.github/`, deploy, auditoría, C1, C2A, pruebas y validadores.
  Incluye también módulos auxiliares de hashing/instrumentación y el receptor
  de formularios, porque pueden alterar el comportamiento bajo prueba.
- Ejecuta el validador de la base si existe. Bloquea cambios o eliminaciones de
  workflows, pruebas y validadores ya presentes en la base. Las incorporaciones
  nuevas reciben advertencia; necesitan revisión independiente.

Las dependencias y AWS CLI 2.36.42 se instalan antes de las pruebas. Esa versión
es obligatoria para la regresión existente de encoding. El contenedor de pruebas
usa `--network none`: solo permite loopback para servidores ficticios; no permite
contactar AWS. No recibe variables AWS, secretos, token de Actions, socket Docker
ni perfiles del host. Monta únicamente el repositorio en solo lectura. Los mocks
de formularios pueden simular escrituras: no son operaciones AWS reales.

El preflight comprueba herramientas del host, arquitectura e historial completo
con base/head disponibles. El contenedor imprime versiones y ejecuta pip check
antes de correr la suite; estos diagnósticos no invocan APIs AWS.

El workflow solo concede `contents: read`, no solicita OIDC, no configura roles
ni ambientes productivos y no invoca el workflow de producción.

## Límites y aprobación independiente

Un workflow `pull_request` puede modificarse en el propio PR. Incluso el paso que
recupera el validador de la base puede ser eliminado por ese PR. Por tanto este
archivo **no es una frontera completa contra un autor malicioso**. El bloqueo de
controles establecidos evita modificaciones accidentales mientras el workflow
se conserva; un check verde por sí solo no demuestra su integridad. Seleccionar
GitHub Actions como fuente del check tampoco vincula el nombre a este archivo.

Antes de tratarlo como control obligatorio, proponer CODEOWNERS para `.github/`,
los scripts sensibles, validadores y suites de pruebas, con un responsable DevOps
con permisos de escritura distinto del autor. Proteger también CODEOWNERS.
Requerir aprobación de propietarios y revisión de los cambios más recientes;
descartar aprobaciones obsoletas y limitar bypass. No se creó CODEOWNERS ni se
modificó el ruleset en este trabajo.

Para permitir mantenimiento de los controles sin aprobarse a sí mismos, preparar
un workflow obligatorio administrado fuera de la rama del PR mediante un ruleset
de organización, si el plan lo permite. Hasta disponer de ese mecanismo, los PRs
que modifiquen controles establecidos fallarán deliberadamente. No renombrar el
check, añadir un bypass por etiqueta ni debilitarlo para promover esos cambios.
La primera incorporación requiere revisión manual completa por DevOps.

El contenedor limita la ejecución de las pruebas, no el workflow entero. La
preparación de dependencias requiere red. Las imágenes base, acciones y paquetes
transitivos no están fijados todos por digest/hash; no ofrece reproducibilidad
criptográfica. No valida IAM, infraestructura, estado real de S3/CloudFront ni
autoriza un apply. No se ha ejecutado Actions ni el contenedor localmente si no
hay Docker disponible.

## Activación posterior en Proteccion-main

1. Revisar e integrar los archivos mediante el proceso humano autorizado. Los
   módulos y pruebas C1/C2A actualmente sin seguimiento también deben formar
   parte de los cambios promovidos; el workflow aislado no basta. Este trabajo
   no hace commit, push ni merge. La integración a `main` dispararía el despliegue
   existente: coordinar esa promoción por separado.
2. Configurar primero el control independiente descrito arriba y revisar permisos
   y bypass. No activar el check como única protección de sus propios archivos.
3. Obtener una ejecución real del check en un PR de prueba y comprobar logs,
   resumen, cero omisiones, rechazo de escrituras y ausencia de configuración AWS.
   Probar también un cambio a una página protegida y la eliminación de un control:
   ambos deben fallar. No fusionar esos casos negativos.
4. En Settings → Rules → Rulesets → `Proteccion-main`, confirmar que el destino es
   `main`, conservar las reglas existentes y agregar el status check exacto
   `PR offline validation`, con GitHub Actions como fuente esperada. Exigir PR y
   la revisión independiente; definir si se requieren ramas actualizadas.
5. Verificar con un PR que no se puede integrar con el check ausente, fallido o
   pendiente y que no existe otro job con el mismo nombre. No usar merge queue
   sin incorporar y probar antes el evento `merge_group`.
6. Guardar evidencia de la configuración y del PR de prueba. Estos pasos son una
   propuesta; no se cambió `Proteccion-main`.

Referencias oficiales:
- [Reglas disponibles, revisiones y fuente del status check](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Creación de rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)

## Reproducción local

Usar Python 3.12, Boto3 1.37.38, PyYAML 6.0.3 y AWS CLI 2.36.42. Las pruebas usan
fixtures y loopback, nunca credenciales productivas:

```powershell
$env:PR_OFFLINE_STRICT = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
python scripts/run_pr_offline.py
actionlint -shellcheck= .github/workflows/validate-pr.yml
```

`actionlint` comprueba YAML, estructura Actions y expresiones. `-shellcheck=`
deshabilita el analizador externo de shell, no la validación de Actions.
Las pruebas de `test_pr_validation.py` verifican contrato YAML, diff Git real,
renombrados, eliminación de controles, rechazo de escrituras y `applyBlocked`.

## Diagnóstico local del 11 de noviembre de 2026

- Rama inspeccionada: `feat/optimize-aws-web-deploy`.
- Suite completa con Python 3.12, Boto3 y AWS CLI 2.36.42: **139 pruebas OK,
  cero omitidas**. Después de ampliar el caso de eliminación de controles se
  repitieron las seis pruebas nuevas: **6 OK**.
- `actionlint` 1.7.7 sobre el YAML final: salida 0, sin diagnósticos.
- `git diff --check`: salida 0. Sin diferencias en `deploy-production.yml`,
  `deploy-aws-production.ps1` ni archivos versionados de `infra/`.
- Docker no está instalado localmente: no se construyó la imagen ni se ejecutó
  el aislamiento Linux. El comportamiento del contenedor necesita la ejecución
  inicial de CI antes de activar el check obligatorio.
- Módulos de auditoría, planificación y sus pruebas ya estaban sin seguimiento
  al empezar; se conservaron. Si faltan al promover los archivos, CI fallará.
- No se ejecutó despliegue, llamadas AWS reales, commit del proyecto, push, merge
  ni modificación de rulesets.
