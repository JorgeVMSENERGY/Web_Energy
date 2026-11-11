# C1: plan incremental y ejecutor simulado, sin escritura AWS

C1 es un prototipo **offline**. `c1_plan.py` y `c1_simulator.py` no tienen
implementación de PutObject, DeleteObject, CreateInvalidation, clientes AWS,
credenciales, bloqueo remoto ni autorización real. Los JSON y diarios son
artefactos locales. Todos los planes, snapshots y diarios tienen
`applyBlocked=true`; no hay opción para cambiarlo o ejecutar un apply.
El workflow, despliegue, IAM, Terraform, S3 y CloudFront existentes no se modifican.

## Fuentes y límites

El reporte real importado es el generado por `audit_s3.py`, terminado el
2026-11-10T22:19:33Z: 947 hashes coincidentes, 947 objetos, 0 drift/faltantes/
sobrantes, `auditComplete=true`, `applyBlocked=true` y commit
`72ad37efaf16c796649c458c273e70984061fdb5`. Cada fila se comprueba contra
SHA256 Git, tamaño leído y los cuatro VersionId (`before`, `get`, `after`,
`final`). También se comprueban cuenta, bucket, identidad, commit, contadores y
estado completo. La entrada incompleta, interrumpida o contradictoria falla.
La huella SHA256 del reporte permite identificar los bytes importados; no es
una firma ni prueba independiente de procedencia. No se copia el ARN al baseline.

El auditor verificó SSE-S3 AES256 pero **no guardó** Content-Type, Cache-Control,
Content-Encoding, Content-Disposition, Content-Language, Expires, metadata de
usuario, etiquetas, clase, ACL, redirección web ni Object Lock de cada versión.
El baseline conserva esos metadatos como `complete=false`, no los inventa a
partir de la extensión o del script de producción. Un NO-OP de bytes no prueba
que tales metadatos sean correctos. Cualquier operación sobre un objeto con
metadatos actuales o históricos incompletos queda bloqueada. Una creación requiere
una política completa explícita. AES256 se registra porque el auditor la exigió.
Un futuro C2 tendrá que capturar y revisar metadatos y política efectiva bajo
exclusión de escritores, incluyendo cambios de tags/ACL que pueden no crear una
versión nueva. La lista de campos de C1 es un modelo a revisar, no prueba de que
cubre todas las opciones de una configuración AWS futura.

La línea base confirmada incluye **ya existentes** en el bucket tanto
`catalogo-proyectos.html` como `proyectos-vms.html`. Se conservan como UNCHANGED
en el NO-OP histórico; C1 bloquea explícitamente CREATE, UPDATE o DELETE de ambas
rutas y no proporciona bypass ni autorización para publicarlas. Los cambios
externos sobre ellas se clasifican como DRIFT y también bloquean el plan.

## Separación de documentos

1. `historical-baseline`, esquema 1: cuenta, bucket, región, distribución,
   commit, huella/fecha del reporte y `files` por clave con SHA256, bytes,
   VersionId y metadatos con grado de completitud. Es evidencia histórica.
2. `remote-observation`, mismo detalle y procedencia separada. Importar un JSON
   no demuestra frescura, exclusión de otros escritores ni autenticidad.
3. Inventario Git deseado: se obtiene con `deploy_plan.inventory`, que lee blobs
   por OID desde un commit fijado y comprueba HEAD; no lee el working tree.
4. `c1-offline-plan`: clasificación, operaciones, metadatos, versiones previas,
   invalidaciones, recuperación y bloqueos. Contiene `authorization.granted=false`,
   `freshEvidenceRequiredBeforeAnyFutureApply=true` y `applyBlocked=true`.
5. Una autorización futura sería otro documento con identidad, propósito, destino,
   huella de plan y evidencia, alcance y vencimiento. C1 no la emite ni acepta.

La comparación tripartita detecta discrepancias baseline/observación por SHA256,
tamaño, VersionId y metadatos si ambos snapshots tienen metadatos completos.
Cambio externo del mismo tamaño, reescritura de mismos bytes con otra versión,
objeto externo nuevo o desaparición remota son DRIFT; bloquean el plan completo y
no se transforman automáticamente en escritura/borrado. Sin baseline u observación
no se proponen operaciones. Sin observación reciente bajo control de escritores,
una comparación offline no demuestra el estado actual. Nunca se usa mtime, ETag
o tamaño como prueba SHA256. ETag podría evaluarse como precondición de concurrencia
en C2, pero no como hash de contenido; por sí solo tampoco prueba versión ni metadata.

En el resto: UNCHANGED no genera acciones; CREATE requiere ausencia en baseline y
observación; UPDATE exige diferencia de bytes respecto del Git deseado; DELETE exige
objeto previamente conocido y ausente del destino. Se publicaría solo lo cambiado.
Archivos excluidos del inventario productivo y staging no se eliminan automáticamente.
`classifications` detalla objetivo, baseline y observación; `operations`, `uploads`,
`deletes`, `invalidations` y `recovery` permiten revisar cada decisión.

El NO-OP se generó con el inventario Git del commit y el mismo reporte auditado
como historia y observación. `sameSourceEvidence=true`,
`evidenceUsage=historical-offline-replay-not-current-proof` y
`freshEvidenceRequiredBeforeAnyFutureApply=true` indican el límite: **no es una
segunda lectura ni demuestra que S3 siga igual**. La simulación da 947 UNCHANGED,
cero uploads/deletes/invalidaciones y siempre applyBlocked=true.

## Publicación y caché: diseño, no aplicación

El orden propuesto es activos no HTML primero, HTML después y DELETE diferido.
Las claves de activos inmutables o versionadas por contenido y compatibilidad
hacia atrás deben preferirse: sobrescribir CSS/JS en la misma clave antes del HTML
puede romper páginas anteriores; un orden por sí solo no garantiza atomicidad.
C2 debe revisar dependencias, clientes con HTML antiguo y compatibilidad, verificar
los uploads mediante lectura/versiones y SHA256, invalidar HTML/activos, esperar
la propagación y un período de gracia conocido para navegadores/CDN, y solo luego
considerar deletes individualmente aprobados. Un DELETE C1 está etiquetado
`DEFERRED_DELETE_AFTER_CACHE_GRACE`; el simulador requiere
`--assume-deletion-grace`, que es una hipótesis ficticia, no una espera real.
La invalidación agregada del simulador no modela las barreras y lotes necesarios
para hacer esto seguro en producción.

Se producen rutas exactas para archivos modificados o borrados, incluidos PDF con
el mismo nombre. `index.html` produce `/index.html` y `/`. El NO-OP no invalida.
Se codifican nombres Unicode/reservados y se bloquean `~`, `*` y rutas demasiado
largas. No se propone `/*`. Deben auditarse rewrite de URL, aliases, default-root,
cache keys, query strings, versiones y TTL antes de C2; un path exacto no cubre
necesariamente variantes de query. S3/Git no invalidan una caché existente por
sí solos. La semántica y restricciones son las de [AWS CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/invalidation-specifying-objects.html).

## Diario, idempotencia y recuperación

`c1_simulator.py` opera únicamente sobre `simulatedRemote` e
`simulatedInvalidations` guardados dentro de un JSON local. `start` crea el
journal exclusivamente; `run` guarda la intención, registra un efecto ficticio y
luego confirma cada paso. El archivo temporal se escribe, hace flush/fsync y se
reemplaza localmente. Un fallo inyectado antes o después del efecto permite probar
reanudación, coincidencia del plan/observación, detección de cambios ajenos,
CallerReference simulado estable y aplicación repetida sin duplicar efectos.
La simulación de borrado exige una suposición explícita de gracia de caché.
El recibo `simulatedEffectId` vive en el mismo archivo que el efecto falso: esto
**no resuelve** una respuesta AWS perdida ni implementa una transacción distribuida.

`recover` invierte efectos simulados en orden inverso: UPDATE/DELETE conservan
`previousVersionId` y modelan restauración de la versión previa como una versión
nueva; CREATE modela retirar el objeto creado. Se documenta una acción de caché
separada tras la recuperación S3, con paths y CallerReference distintos. Recuperar
S3 no recupera automáticamente la caché ni elimina los datos que ya llegaron a
navegadores. Un diario recuperado no se reanuda hacia delante. El journal es un
snapshot mutable de simulación, no un log autenticado ni un lock entre procesos.

C2 requeriría intención durable antes de cada efecto real, IDs y VersionId devueltos,
verificación posterior, estados `prepared/sent/verified/cache-pending/completed`,
reconciliación del resultado ambiguo sin asumir propiedad del cambio externo,
CallerReference estable para cada lote y nueva invalidación al recuperar. AWS
documenta la idempotencia de un CallerReference cuando la petición es idéntica:
[InvalidationBatch](https://docs.aws.amazon.com/cloudfront/latest/APIReference/API_InvalidationBatch.html).
No eliminar versiones históricas para simular rollback. Versioning puede crear una
nueva versión o delete marker; confirmar la mecánica y permisos antes de C2.

## Exclusión mutua y almacenamiento privado propuestos

Un `concurrency` de Actions o journal local no bloquea escritores manuales,
otros roles, replicación, jobs externos o un worker antiguo. Propuesta C2, aún
sin implementar: bucket privado separado **por confirmar y verificar disponibilidad**
`vmsenergy-web-state-787140332697`, región us-east-1, sin acceso público ni origen
CloudFront; prefijos `web-energy/prod/baselines/`, `observations/`, `plans/`,
`authorizations/`, `journals/` y `confirmed/`, versionado, cifrado, retención y
control de integridad. No usar el bucket web ni servir estado como contenido.

Para coordinación, estudiar un lock de DynamoDB con adquisición condicional,
lease, heartbeat, propietario e identificador de época, y compare-and-swap del
puntero confirmado. El [lock de DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/BestPractices_DistributedLocking.html)
coordina clientes que cooperan; **S3 no valida automáticamente ese token**. Un
worker cuya lease expiró o un escritor externo con credenciales aún válidas podría
escribir después. Antes de C2 se necesita un mecanismo verificable que impida
operaciones de todos los demás escritores y de workers antiguos en el punto de
escritura, o una ventana de mantenimiento con aislamiento/revocación efectivos.
Incluir roles humanos, CI, automatizaciones y rutas de emergencia; si no puede
probarse, permanecer bloqueado. Conditional writes/deletes pueden reducir carreras,
pero `If-Match` de ETag no resuelve ABA ni garantiza exclusión global:
[escrituras condicionales S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[borrados condicionales S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-deletes.html).
No se propone una política IAM o infraestructura lista para aplicar.

## Permisos y costos a evaluar en C2

Lectura web: STS, `s3:ListBucket`, `s3:GetObject`; para evidencias de propiedades,
versiones y recuperación evaluar `s3:GetBucketVersioning`, `s3:GetObjectVersion`,
`GetObjectTagging`/`GetObjectVersionTagging` y ACL solo si corresponden al modelo
real. El recopilador vigente no obtiene todos esos datos. Para escritura futura,
revisar alcance exacto de `s3:PutObject`, `s3:DeleteObject`, versionado/copia y,
si procede, permisos de tags/ACL y KMS. Invalidación futura limitaría
`cloudfront:CreateInvalidation` y consulta a la distribución indicada. El bucket
de estado necesitaría List/Get/Put acotados al prefijo; DynamoDB, operaciones
condicionales del lock; el cifrado KMS, permisos de clave correspondientes.
No se inspeccionaron ni cambiaron permisos reales, y esta lista no autoriza nada.

Costos propuestos: almacenamiento y solicitudes del bucket privado y versiones
históricas; lecturas GET/HEAD/LIST y transferencia de la evidencia completa;
PUT/DELETE/copias de recuperación; KMS, DynamoDB y logging si se eligen. La
retención de versiones aumenta almacenamiento; no se estima importe sin retención,
volumen, tarifas y frecuencia. La auditoría existente registró 947 GET, 2841 HEAD
y 2 LIST. AWS documenta costos de [S3](https://aws.amazon.com/s3/pricing/) y
[DynamoDB](https://aws.amazon.com/dynamodb/pricing/). CloudFront documenta 1.000
paths de invalidación gratuitos por mes agregados en la cuenta y cargo por encima;
consultar tarifas y consumo reales: [cobro de invalidaciones](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/PayingForInvalidation.html).
C1 no realiza esas operaciones ni calcula un costo de despliegue real.

## Ejecución offline y archivos producidos

```powershell
# Comparación reproducible del reporte histórico contra sí mismo; no consulta AWS.
$Audit = Join-Path $env:TEMP 'vms-full-sha256-ff1ee2cf3dc94f2b9b2f9c62d72168aa.json'
$Plan = Join-Path $env:TEMP ('c1-plan-' + [guid]::NewGuid().ToString('N') + '.json')
$Base = Join-Path $env:TEMP ('c1-base-' + [guid]::NewGuid().ToString('N') + '.json')
$Observed = Join-Path $env:TEMP ('c1-observed-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/c1_plan.py --historical-audit "$Audit" --observed-audit "$Audit" --repo . --output "$Plan" --baseline-output "$Base" --observation-output "$Observed"
$Journal = Join-Path $env:TEMP ('c1-journal-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/c1_simulator.py start --plan "$Plan" --observation "$Observed" --journal "$Journal"
python -B scripts/c1_simulator.py run --journal "$Journal"
# Para un diario de ejemplo interrumpido: python -B scripts/c1_simulator.py recover --journal "$Journal"
```

También se puede planificar desde archivos ya convertidos con `--baseline` y
`--observation`; todos siguen siendo históricos y no son autorizaciones.
Los artefactos en `scripts/c1_simulations/` son:

- `baseline-auditado.json` y `observacion-auditada.json`: documentos separados,
  extraídos del mismo reporte; los VersionId son reales, metadatos desconocidos.
- `noop-auditado.json`: 947 UNCHANGED, sin acciones.
- `casos-sinteticos.json`: CSS, PDF, HTML, CREATE, DELETE, DRIFT, staging y
  evidencia ausente; hashes, VersionId y metadatos de estos casos son ficticios.
- `fallo-recuperacion-simulados.json`: estados locales de fallo parcial,
  recuperación S3/caché separada y reintento idempotente, todos simulados.

Estos archivos contienen claves y VersionId; tratarlos como datos operativos y
revisar política de manejo antes de compartirlos. No incluyen credenciales ni ARN.
Los ejemplos sintéticos no demuestran permisos AWS, seguridad de concurrencia,
invalidez de caché ni recuperación real.

## Paso propuesto a C2, todavía desactivado

1. Recopilar evidencia nueva completa y procedencia verificable, metadatos y
   configuración CloudFront reales; resolver explícitamente los dos archivos
   de staging ya existentes y los metadatos históricos desconocidos.
2. Diseñar y probar en un entorno aislado la exclusión de todos los escritores,
   fencing efectivo, diario durable, precondiciones, recuperación de respuesta
   ambigua, retención/versiones y manejo de caché con variantes y alias.
3. Revisar el plan y sus permisos/costos con mínimos privilegios; aplicar pruebas
   de fallos/seguridad y simulaciones con escritores hostiles. Diseñar autorización
   separada y explícita, firmada o equivalente, vinculada a la evidencia vigente.
4. Solo tras esa revisión y una decisión separada, considerar un ejecutor real;
   C1 no cambia el workflow ni activa C2.

## Validaci?n offline ejecutada

`python -B -m unittest discover -s scripts -p test_*.py -v`: **98 pruebas aprobadas** en 60,728 s, sin omisiones (incluye 18 pruebas C1).

`python -B -m unittest discover -s infra/forms/tests -p test_*.py -v`: **23 pruebas aprobadas** en 0,355 s. Total **121**. Integraciones SDK/CLI usan exclusivamente loopback; C1 no hace llamadas AWS.

Se ejecutaron `start` y `run` del simulador sobre el NO-OP auditado con diario temporal local: `simulated-complete`, cero operaciones e invalidaciones y `applyBlocked=true`. `git diff --check` pas? y no aparecen cambios rastreados en workflow, despliegue o infraestructura.
