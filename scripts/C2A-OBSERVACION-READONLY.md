# C2A: observación de solo lectura y plan bloqueado

C2A se implementó y probó offline. **Codex no ejecutó consultas AWS reales.**
No hay PutObject, DeleteObject, CopyObject, invalidaciones, modificaciones IAM,
Terraform ni workflow en C2A. `applyBlocked=true` en observaciones, planes y
revalidaciones; `authorization.granted=false`. C2B no está implementado.

## Flujo y evidencia

1. Se valida que `baseline-auditado.json` sea exactamente el documento C1
   reconstruido desde el reporte completo `audit_s3.py`: cuenta, bucket, commit,
   SHA256, tamaño, cuatro VersionId y huella del reporte. No se aceptan cambios
   arbitrarios al baseline ni se actualiza automáticamente. Este cotejo detecta
   modificaciones accidentales, no autentica criptográficamente un reporte que
   alguien pueda falsificar junto con su baseline; su procedencia requiere control
   independiente. El reporte anterior tiene `auditComplete=true` pero no es por
   sí solo evidencia de estado actual.
2. Se inventaría el commit Git objetivo mediante `deploy_plan.inventory` y
   `git_batch.py`. Es un documento separado de la evidencia histórica.
3. Solo con `--read-s3`, `ReadOnlyC2` reutiliza `Boto3Reader`, `audit_s3.listing`
   y `hash_object`. Reutiliza el perfil/SSO, clientes, región y endpoints fijados;
   STS comprueba cuenta y ARN. Lista S3 por páginas con EncodingType=url explícito
   y decodificación estricta exactamente una vez. En cada clave visible hace HEAD,
   registra VersionId, ContentLength, ETag opaco y valores/presencia de ContentType,
   CacheControl, ContentEncoding, ContentLanguage, ContentDisposition, Expires,
   Metadata, SSE, SSEKMSKeyId, StorageClass y atributos HEAD adicionales. Un campo
   omitido se guarda `present=false`, no como un valor por defecto supuesto.
4. Solo si **clave y VersionId** coinciden con el baseline y el tamaño no contradice
   el informe auditado, se reutiliza su SHA256. ETag y tamaño nunca se usan para
   demostrar igualdad de bytes. Una versión nueva o clave nueva queda con SHA256
   desconocido, sin operación propuesta. `--verify-unknown` habilita GET streaming
   y SHA256 solo para esas claves, comprobando versión/longitud; no incorpora los
   hashes o versiones nuevos al baseline. GET exige la política AES256 actual del
   auditor; una versión con SSE distinta se aborta, no se considera verificada.
5. Una segunda enumeración completa y HEAD final de cada objeto deben coincidir
   con los primeros en clave, tamaño, VersionId, ETag y todos los campos HEAD
   capturados. Cambios, permisos insuficientes, truncamiento, error de GET o
   interrupción dejan observación incompleta y bloqueada; no se conservan secretos
   del error. No hay reintento automático (una tentativa SDK); para reintentar se
   empieza una observación nueva con salidas nuevas.
6. La observación guarda tiempos UTC inicio/fin, cuenta, ARN, bucket, región,
   commit objetivo, `observationId` (SHA256 canónico) y vencimiento de 1–900 s
   después del fin (predeterminado 300). El plan registra `planChecksum`,
   `observedAt`, `evidenceExpiresAt`, diagnóstico, tokens y el plan C1 cuando
   todos los SHA256 son conocidos. El checksum detecta alteraciones, **no es una
   firma ni una autorización**. El ARN y metadatos son datos operativos; guardar
   los reportes en un directorio privado y no publicar credenciales/headers.
7. `--revalidate-plan` exige plan no vencido, mismo baseline y mismo commit; crea
   otra observación posterior y compara la huella de **cada fila completa**,
   incluyendo metadata HEAD, VersionId, ETag y ausencia/delete marker. Si cambió
   algo devuelve `CHANGED_NEW_PLAN_REQUIRED`. Una observación anterior no sirve
   como revalidación. Aun con `NO_CHANGE_STILL_BLOCKED`, falta exclusión de
   escritores y autorización. Si el plan vence durante la relectura se rechaza.

El listado doble y dos HEAD no constituyen una transacción. Las claves se leen
secuencialmente y la primera puede cambiar antes de finalizar la última. El TTL
empieza al terminar la observación, no garantiza que cada objeto se haya visto
entonces. Un escritor puede actuar después del último HEAD o entre revalidación
y una acción futura; tags/ACL pueden cambiar sin cambiar VersionId. La
revalidación debe realizarse bajo una exclusión efectiva de escritores que C2A
**no implementa ni verifica**. `writerExclusionEstablished=false` siempre.

## Diagnóstico de diferencias

| Situación | Diagnóstico C2A |
| --- | --- |
| Clave y VersionId coinciden | `UNCHANGED_VERSION`, SHA256 del baseline |
| Versión distinta sin GET | `UNKNOWN_VERSION`, SHA256 nulo |
| Versión distinta y GET igual | `EXTERNAL_VERSION_SAME_SHA256`: drift por versión |
| Versión distinta y GET diferente | `CONTENT_DRIFT`, incluso del mismo tamaño |
| Content-Type/Cache-Control u otro HEAD comparable cambia | `HEAD_METADATA_DRIFT` |
| Clave no presente en baseline | `EXTERNAL_ADDITION` |
| Clave histórica ausente | `MISSING_OR_DELETE_MARKER_UNRESOLVED` |
| Marker actual confirmado opcionalmente | `DELETE_MARKER` con VersionId |

Para una clave histórica ausente, `--check-delete-markers` usa
`ListObjectVersions` paginado por prefijo, comprueba la clave exacta y `IsLatest`,
y repite la consulta tras el listado final. Requiere `s3:ListBucketVersions` y
puede recorrer múltiples páginas. Sin esa opción, ausencia y delete marker son
indistinguibles. Incluso con ella solo se inspeccionan claves del baseline que
faltan del listado vivo: markers de claves ajenas nunca vistas no se enumeran.
Un HEAD 404 con `x-amz-delete-marker` es otra señal posible según [AWS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/DeleteMarker.html),
pero no se convierte un 403/404 genérico en prueba de marker. La codificación de
markers/paginación sigue [ListObjectVersions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectVersions.html).

`ContentType` y `CacheControl` cambian el diagnóstico si el valor histórico
comparable existe. El baseline real C1 no los guardó: esa comparación histórica
queda incompleta, aunque sí se detectan cambios de dichos campos entre los dos
HEAD y durante una revalidación nueva. Tampoco se inventa la clase STANDARD si
HeadObject la omite. Los valores HEAD actuales no permiten reconstruir el pasado.
Las rutas `catalogo-proyectos.html` y `proyectos-vms.html` siguen protegidas por
C1: no se proponen operaciones sobre ellas.

Con el baseline real, aun cuando los 947 VersionId coincidan, el resultado puede
mostrar `contentComparison=NO-OP` y 947 UNCHANGED en el plan C1, pero el plan C2A
queda `BLOCKED`: metadata histórica incompleta, atributos no HEAD sin consultar,
exclusión de escritores no demostrada y autorización/revalidación no implementadas.
No se ha observado S3 en esta fase, por lo que no hay diagnóstico actual ni NO-OP
real. `c2a_simulations/simulacion-947-versiones.json` es una **simulación local**:
utiliza las claves/versiones históricas, pero HEAD, ETag y metadatos son ficticios.
No es evidencia remota. `revisar-escritores-no-ejecutado.json` registra que las
políticas y los escritores efectivos no fueron consultados.

## Atributos y permisos no cubiertos por HEAD

HEAD/list/GET requieren `s3:ListBucket` y `s3:GetObject` en los recursos exactos;
Identity Center puede necesitar sus propias llamadas de credenciales. `s3:ListBucketVersions`
es adicional solo para la opción de markers. Para tags y versiones puede requerirse
`GetObjectTagging`/`GetObjectVersionTagging`; para ACL, `GetObjectAcl`/versión;
para versión del bucket, ownership y política, las acciones de lectura respectivas.
Object Lock, retención, controles de cuenta, KMS, replicación y acceso por puntos
S3 pueden requerir permisos distintos. Los atributos de CloudFront requieren sus
APIs de distribución/cache policy. No hay inferencia a partir de valores omitidos:
los no consultados figuran en `nonHeadAttributesNotCollected`. Un 403 es evidencia
insuficiente, no ausencia del dato. `GetObjectTagging` o ACL pueden cambiar sin
crear versión de contenido; deben recopilarse bajo coordinación de escritores.

## Comandos preparados para el operador: NO ejecutados por Codex

Requiere Python y `python -m pip install -r scripts/requirements-audit.txt`, sesión
SSO vigente y el reporte completo histórico existente. Desde la raíz del proyecto:

```powershell
$Base = 'scripts/c1_simulations/baseline-auditado.json'
$HistoricalAudit = Join-Path $env:TEMP 'vms-full-sha256-ff1ee2cf3dc94f2b9b2f9c62d72168aa.json'
$Observation = Join-Path $env:TEMP ('vms-c2a-observation-' + [guid]::NewGuid().ToString('N') + '.json')
$Plan = Join-Path $env:TEMP ('vms-c2a-plan-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/c2a_observe.py --read-s3 --baseline "$Base" --baseline-audit "$HistoricalAudit" --profile vms-web-infra --repo . --ttl-seconds 900 --observation-output "$Observation" --plan-output "$Plan"
$Code = $LASTEXITCODE
Write-Host "Código: $Code; observación: $Observation; plan: $Plan"
```

Este comando lee identidad, dos listados completos y dos HEAD por objeto actual;
no descarga cuerpos si no se añade `--verify-unknown`. Si hay versiones nuevas,
primero quedan desconocidas y bloqueadas. Otra ejecución con reporte nuevo y
`--verify-unknown` descargará **cada** objeto nuevo/de versión cambiada, pudiendo
ser hasta el bucket completo. `--check-delete-markers` agrega listados de versiones
para claves históricas ausentes y requiere permisos adicionales. Los dos switches
son independientes. Los caminos de salida son exclusivos y no se sobreescriben;
se reservan antes de la primera llamada AWS. Si falla un permiso o hay cambios,
revisar el reporte incompleto y volver a comenzar con nombres nuevos.

Revalidación explícita antes del vencimiento, con nuevos archivos:

```powershell
$NextObservation = Join-Path $env:TEMP ('vms-c2a-recheck-' + [guid]::NewGuid().ToString('N') + '.json')
$Revalidation = Join-Path $env:TEMP ('vms-c2a-validation-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/c2a_observe.py --read-s3 --baseline "$Base" --baseline-audit "$HistoricalAudit" --profile vms-web-infra --repo . --revalidate-plan "$Plan" --ttl-seconds 900 --observation-output "$NextObservation" --plan-output "$Revalidation"
```

Usar en la revalidación las mismas opciones `--verify-unknown` y
`--check-delete-markers` que en la observación original. Los tiempos de escaneo
pueden superar el TTL: si el plan expira durante la relectura, se rechaza y se
necesita un plan nuevo. El máximo de 900 s no se extiende automáticamente. El
script devuelve 0 si completó la lectura, incluso si el plan sigue BLOCKED; se
deben inspeccionar `status`, `blockers` y `applyBlocked`.

## Revisión read-only de escritores: pendiente de ejecutar

No se ejecutaron consultas de IAM, SSO, Organizations, bucket, GitHub ni CloudTrail.
**No sabemos qué identidades pueden escribir actualmente.** Los comandos siguientes
son una guía para un operador con permisos de lectura; no modifican políticas.
Pueden devolver datos sensibles de la organización: guardar la salida de forma
privada. Un AccessDenied significa visibilidad incompleta, no ausencia de permisos.

```powershell
$Profile = 'vms-web-infra'
$Bucket = 'vmsenergy-web-prod-787140332697'
aws sts get-caller-identity --profile "$Profile" --region us-east-1
aws s3api get-bucket-policy --bucket "$Bucket" --profile "$Profile" --region us-east-1
aws s3api get-bucket-policy-status --bucket "$Bucket" --profile "$Profile" --region us-east-1
aws s3api get-public-access-block --bucket "$Bucket" --profile "$Profile" --region us-east-1
aws s3api get-bucket-ownership-controls --bucket "$Bucket" --profile "$Profile" --region us-east-1
aws s3api get-bucket-versioning --bucket "$Bucket" --profile "$Profile" --region us-east-1
aws iam list-roles --profile "$Profile"
aws iam list-users --profile "$Profile"
aws iam list-open-id-connect-providers --profile "$Profile"
# Para cada rol candidato, incluyendo OIDC de GitHub y AWSReservedSSO:
$Role = '<nombre-del-rol>'
aws iam get-role --role-name "$Role" --profile "$Profile"
aws iam list-attached-role-policies --role-name "$Role" --profile "$Profile"
aws iam list-role-policies --role-name "$Role" --profile "$Profile"
# Para cada política inline y cada versión predeterminada de política administrada:
aws iam get-role-policy --role-name "$Role" --policy-name '<policy-inline>' --profile "$Profile"
aws iam get-policy --policy-arn '<arn-politica>' --profile "$Profile"
aws iam get-policy-version --policy-arn '<arn-politica>' --version-id '<version-default>' --profile "$Profile"
# Para usuarios manuales, revisar políticas propias y de cada grupo:
aws iam list-attached-user-policies --user-name '<usuario>' --profile "$Profile"
aws iam list-user-policies --user-name '<usuario>' --profile "$Profile"
aws iam list-groups-for-user --user-name '<usuario>' --profile "$Profile"
aws iam list-attached-group-policies --group-name '<grupo>' --profile "$Profile"
aws iam list-group-policies --group-name '<grupo>' --profile "$Profile"
```

También revisar documentos inline de usuario/grupo (`get-user-policy`,
`get-group-policy`), permission boundaries, session policies, trust de roles
OIDC (issuer, `aud`, `sub`, rama/ambiente), cuentas externas en bucket policy,
access points y sus políticas (`s3control`), SCP/RCP de Organizations, resource
policies y KMS. Para SSO, con la región de Identity Center y permisos adecuados,
`aws sso-admin list-instances`, `list-permission-sets` y
`list-account-assignments` permiten revisar asignaciones a la cuenta; revisar
además membresías de grupos del identity store y permisos administrados/inline.
Revisar workflows locales de GitHub, secretos/variables y protección de ambientes;
si se usa `gh api` para permisos de repositorio/ambientes es otra consulta de
lectura que el operador debe ejecutar con la cuenta y repositorio correctos.
No publicar tokens ni valores de secretos. El simulador IAM tiene limitaciones de
políticas de recursos para roles y no prueba todas las condiciones reales:
[limitaciones AWS IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html).
CloudTrail solo ayuda si los eventos de datos y el período pertinente estaban
habilitados. Enumerar identidades locales sin políticas, SCP, acceso externo,
roles asumibles y sesiones no constituye un inventario efectivo completo.

## Contrato de precondiciones: diseño C2B, no ejecutable

- CREATE: `PutObject` con `If-None-Match: *`, validar objetivo/alcance y revisar
  previamente versiones/delete markers. S3 puede aceptar la condición si la
  versión actual es un delete marker; no demuestra que la clave nunca existió.
- UPDATE: `PutObject` con `If-Match` del ETag recién observado; guardar y
  comprobar VersionId anterior y el nuevo. ETag es un token opaco, no SHA256 ni
  una comparación atómica de VersionId o metadata.
- DELETE: `DeleteObject` con `If-Match` del ETag recién observado, versión previa,
  revisión de dependencias, gracia de caché y aprobación explícita. Un borrado
  simple en bucket versionado normalmente crea delete marker.

Los `conditionalContracts`/`preconditionsByKey` son texto y datos de diseño,
`implemented=false`. El servidor puede responder 409/412/404: detener y volver a
observar, nunca forzar. Confirmar soporte del SDK/CLI concreto y política antes de
C2B. [AWS escrituras condicionales](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[borrados condicionales](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-deletes.html).
ETag puede repetirse cuando se reescriben bytes iguales (ABA), no cubre tags/ACL,
y una verificación separada del VersionId no es CAS atómico. Si un escritor no
cooperativo puede operar entre HEAD y acción, no existe garantía global.

Recuperar con una versión anterior requeriría revisar `GetObjectVersion`,
`PutObject`/copia, tags/ACL/SSE y que la versión siga retenida. En una ejecución
futura copiar una versión antigua crea otra versión actual; borrar un VersionId
específico puede eliminarla permanentemente. La recuperación de S3 y la invalidez
pendiente de CloudFront son operaciones distintas; clientes ya cacheados pueden
seguir viendo datos antiguos. No se implementa ni ensaya una recuperación AWS.
[AWS: restaurar versiones](https://docs.aws.amazon.com/AmazonS3/latest/userguide/RestoringPreviousVersions.html).

## C2B: estado privado, coordinación y permisos

Se mantiene como **propuesta**, sin crear recursos: bucket privado versionado
`vmsenergy-web-state-787140332697` (nombre sujeto a disponibilidad) con prefijo
`web-energy/prod/` y subprefijos para baseline, observación, plan, autorización,
diario y puntero confirmado; cifrado, retención, integridad, acceso mínimo y sin
CloudFront. Diario append-only/condicional con intención durable, observación
previa, respuestas ambiguas, VersionId anterior/nuevo, confirmación y estado de
invalidación. Los JSON locales C1/C2A no son journal remoto ni autorización.

Un lock con lease/heartbeat en DynamoDB o exclusión de GitHub Actions solo coordina
participantes. S3 no aplica automáticamente un token de fencing de DynamoDB. C2B
necesita demostrar la exclusión de operadores, otros roles/cuentas, automatismos,
replicación y workers con lease expirada mediante controles efectivos en el punto
de escritura; si no puede demostrarla, sigue bloqueado. Permisos futuros de
escritura web, estado, CloudFront, KMS y lock deben definirse y revisarse por
separado después de la auditoría de identidades; aquí no se conceden ni prueban.

## Estimación de costos de lectura

Si siguen existiendo 947 objetos y el listado cabe en una página: **1 STS, 2 LIST,
1.894 HEAD y 0 GET de cuerpos** con las opciones por defecto. Con K versiones
nuevas y `--verify-unknown`, agregar K GET y los bytes totales de esos K objetos;
si todas cambian, puede volver a descargar ~602.626.178 bytes según el baseline
histórico, no necesariamente el tamaño actual. Con M claves ausentes y la opción
de markers, agregar dos recorridos `ListObjectVersions` por clave, con paginación.
Una revalidación completa repite las lecturas. Retries externos, errores, redirects,
SSO, CloudTrail, redes y políticas pueden alterar el costo.

Como ilustración, **si** aplicaran los precios S3 Standard de referencia de AWS de
USD 0,0004/1.000 GET/HEAD y USD 0,005/1.000 LIST, esas 1.894 HEAD + 2 LIST serían
aprox. **USD 0,000768** en solicitudes, antes de impuestos/franquicias y sin GET,
transferencia, almacenamiento, otras clases o servicios. No se conoce la clase
real si HEAD la omite; consultar la región, clase y tarifas vigentes antes de
operar. [Ejemplo de tarifas AWS](https://aws.amazon.com/blogs/storage/run-spark-31-faster-and-optimize-compute-costs-with-amazon-s3-express-one-zone-on-amazon-emr/),
[precios S3](https://aws.amazon.com/s3/pricing/). No se generan cargos de
invalidación desde C2A.

## Validaci?n final offline

`python -B -m unittest discover -s scripts -p test_*.py -v`: **110 pruebas aprobadas** en 63,171 s, sin omisiones; incluye 12 pruebas C2A.

`python -B -m unittest discover -s infra/forms/tests -p test_*.py -v`: **23 pruebas aprobadas** en 0,186 s. Total **133**. La integraci?n SDK usa ?nicamente 127.0.0.1 con credenciales ficticias; los delete markers Unicode tambi?n se comprobaron por el parser real.

No hubo lecturas ni escrituras AWS por Codex. No existe un NO-OP de estado remoto actual ni una auditor?a efectiva de escritores para C2A. `git diff --check` pas?; no hay diferencias rastreadas en workflow, despliegue o infraestructura y HEAD sigue fijado.
