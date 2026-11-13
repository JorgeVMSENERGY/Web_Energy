# Fase A: planificador offline

No hay cliente AWS ni modo apply. El workflow y el script productivos permanecen intactos.
Requiere Python 3.8+ y Git; funciona sin paquetes adicionales.

```powershell
python scripts/deploy_plan.py --repo .
python -m unittest discover -s scripts -p test_deploy_plan.py -v
python scripts/deploy_plan.py --observed remote.json --confirmed confirmed.json --force-reconcile
```

La salida contiene inventario y plan JSON deterministas. Sin evidencia remota el
plan queda bloqueado; no se inventan operaciones. Se usa HEAD resuelto a commit,
no el índice ni los archivos modificados o sin rastrear. Los blobs se leen con
`git cat-file blob` usando subprocess binario: conserva exactamente Git incluso
en checkout CRLF, con binarios y atributos export-ignore/export-subst. `git archive`
no se eligió porque esos atributos pueden omitir o transformar archivos. Una futura
materialización deberá escribir esos mismos blobs con write_bytes y verificar SHA-256.
Esta fase genera hashes; no crea todavía un directorio de artefactos desplegable.

## Inventario y evidencia

Exclusiones equivalentes al script actual: .github, .claude, infra, scripts, _dist,
_nucleo-wordpress, assets - copia, vms_web_backup; .gitignore,
assets/img/solar.zip, Especialidades, stand-rim-2026 3.html; extensiones .md/.csv
y nombres *.bak*. Comparación sin distinguir mayúsculas como PowerShell.
No se añaden exclusiones nuevas. Symlinks/submódulos publicables y rutas inseguras
fallan cerrados. Diferencias entre índice y HEAD deben resolverse antes de activar.

Cada documento tiene `schema: 1`, `files: {clave: {sha256: hex, size: entero}}`.
El inventario incluye commit. La evidencia remota añade `complete: true` y
`verification: "download-sha256"`. Es un contrato de entrada local, no una prueba
criptográfica de procedencia: el futuro recolector debe listar con paginación completa,
descargar y hashear todos los objetos actuales, registrar bucket/cuenta/versiones
y rechazar lecturas parciales o cambios concurrentes. No aceptar ETag, fecha, tamaño
ni metadata SHA declarada por el uploader como sustituto de los bytes.

El plan compara destino con evidencia real; el manifiesto solo identifica drift.
Altas, cambios de igual tamaño, faltantes y drift producen uploads. Ausentes del
destino producen deletes solo dentro del ámbito publicable; objetos excluidos se
reportan para revisión, nunca se borran automáticamente. Esto es deliberadamente
más conservador que sync --delete: su inventario fuente no cambia, pero ese comando
puede borrar objetos remotos excluidos del inventario fuente.
Manifiesto ausente permite proponer recuperación desde evidencia completa; corrupto
aborta y exige investigación o regeneración explícita sin ese archivo. Uno obsoleto
no gobierna las operaciones. Force solicita reconciliación completa de evidencia,
sin reescribir contenido que ya coincide. Siempre se requiere nueva verificación
antes de una futura aplicación; los planes offline no son autorizaciones ejecutables.

## Arquitectura y migración propuesta (no implementada ni activada)

Guardar estado en bucket privado separado, versionado y cifrado, nunca en la raíz
publicable: manifests inmutables por despliegue y puntero confirmed actualizado por
comparación condicional. Confirmar ubicación y nombre antes de provisionar.
Inicializar descargando y verificando todo S3, clasificar objetos ajenos, contrastar
con Git y revisar drift; nunca asumir que S3 corresponde a HEAD por cuatro muestras.
El primer manifiesto describe el estado verificado real, no el destino deseado.

Secuencia futura: bloqueo compartido de escritor; verificar identidad y evidencia;
plan revisado; diario pendiente durable con claves, hashes y versiones anteriores;
uploads verificados, luego deletes revisados; relectura completa; invalidaciones;
esperar éxito; publicar manifiesto y cambiar confirmed condicionalmente. No confirmar
si falla cualquier etapa. Guardar la unión de invalidaciones pendientes en el diario:
un reintento puede encontrar bytes ya actualizados y aun necesitar invalidar caché.
Retomar diario y comprobar estado real tras interrupción; no repetir ciegamente un plan.
S3 Versioning permite recuperar versiones anteriores si se conservan IDs y permisos.
El bloqueo de Actions no detiene escritores manuales: restringirlos o coordinar bloqueo
externo. Invalidar /index.html y / cuando cambia index; codificar UTF-8 y caracteres
reservados, incluido asterisco, sin comodines generales. Revisar aliases, query strings,
reescrituras y claves con ~ (CloudFront no las admite en invalidaciones) antes de activar.
Dividir lotes según cuotas, usar JSON y CallerReference estable por lote/diario.

Permisos adicionales a contrastar con IAM actual: ListBucket y GetObject para evidencia
web; ListBucketVersions/GetObjectVersion para recuperación; GetObject/PutObject y
ListBucket limitados al estado privado, sin DeleteObject del estado; permisos KMS si
se elige SSE-KMS; permisos de bloqueo según mecanismo. Mantener escritura/borrado web
limitados al ámbito aprobado y CreateInvalidation/GetInvalidation a E3FWVCRXQ69DI6.
No se ha consultado ni cambiado IAM. Validar soporte de escrituras condicionales en
cliente/políticas y evitar bypass del puntero confirmado.

El workflow futuro primero compara inventarios de blobs y política. Cambios solo en
infra/docs no publican contenido ni invalidan; workflow_dispatch con force ejecuta
verificación completa aun sin cambios Git. No depender del commit anterior ni solo
de filtros paths: verificar semánticamente inventario. Mantener el workflow actual
hasta validar el alternativo en pruebas aisladas y simulaciones Windows/Ubuntu.

Falta antes de activar: auditoría completa S3; evidencia con procedencia, versiones y
caducidad; política IAM real; ubicación del estado; control de escritores concurrentes;
metadatos Content-Type/Cache-Control/Content-Encoding; comportamiento CloudFront,
aliases y query strings; límites y tratamiento de claves especiales; ejecutor con
diario/rollback/reintentos/confirmación atómica y tests de integración en entorno aislado.
Esta fase compara contenido, no corrige drift de metadatos. Validar paridad del
inventario con el script actual y ejecutar pruebas nativas en Ubuntu antes de migrar.

## Fase B: comparación offline de metadatos

```powershell
python -B scripts/deploy_plan.py --repo . --s3-metadata "$env:TEMP\vms-web-s3-inventory.json" --output scripts/deploy-metadata-report.json
python -B -m unittest discover -s scripts -p test_deploy_plan.py -v
```

`--output` crea exclusivamente un archivo nuevo, nunca sobrescribe archivos existentes.
El reporte incluye el inventario SHA256 de blobs HEAD, el plan bloqueado si no hay
evidencia SHA256 remota, y metadataComparison. Las cuatro categorías contienen claves
exactas (sensibles a mayúsculas) y gitSize/s3Size; null significa que esa clave no está
en ese lado. Los sobrantes incluyen todos los objetos del snapshot, incluso excluidos
del despliegue; no son órdenes de borrado. El reporte queda dentro de scripts, excluido
del inventario publicable. No se altera ninguna exclusión.

El lector admite JSON UTF-8 con o sin BOM, rechaza sintaxis inválida, miembros JSON
duplicados incluso anidados, NaN/Infinity, claves de objetos duplicadas, tamaños no
enteros/negativos/booleanos, entradas incompletas y claves inseguras. Rechaza truncamiento
explícito, tokens de continuación, prefijos parciales, delimitadores, CommonPrefixes
no vacíos y EncodingType=url (no convierte silenciosamente claves codificadas).
KeyCount y MaxKeys, si existen, deben ser consistentes con Contents. Cualquier error
aborta con código distinto de cero antes de generar reporte o calcular blobs.

Un agregado CLI puede omitir IsTruncated. En ese caso solo se permite un diagnóstico
del archivo recibido: coverage=unproven-missing-IsTruncated y applyBlocked=true.
No demuestra cobertura completa de S3; los faltantes/sobrantes son relativos al
snapshot. Incluso IsTruncated=false solo declara una lista raíz no truncada, no
autentica procedencia, actualidad, bucket ni ausencia de modificaciones concurrentes.
Nunca se crea complete=true ni verification=download-sha256 desde metadatos.
La coincidencia de clave/tamaño no prueba igualdad de contenido ni ausencia de drift:
un cambio manual de igual tamaño permanece indetectable en esta comparación.

### Auditoría SHA256 futura (propuesta, no ejecutada)

1. Identificar cuenta/bucket y habilitación vigente de Versioning. Obtener bloqueo de
   escritores que incluya operadores manuales; no basta la concurrency de Actions.
   Registrar inicio de auditoría y evidencia de todas las páginas ListObjectsV2 de la
   raíz sin filtros, con tokens encadenados hasta IsTruncated=false y claves únicas.
2. Obtener HeadObject de cada clave actual, sin especificar versión, y registrar el
   VersionId inicial V0. Fallar si falta, es null o aparece delete marker/404/403/error.
   ListObjectsV2 no proporciona VersionId. Según el requisito posterior del recolector,
   descargar GetObject actual sin versionId (solo GetObject, sin GetObjectVersion);
   exigir VersionId devuelto igual a V0, validar longitud y SHA256 sobre todos los bytes
   de respuesta sin transformaciones, no sobre ETag o checksum declarado.
3. Repetir HeadObject de la versión actual sin versionId y exigir V1=V0. Un HEAD de
   la versión fijada no detectaría cambios en la versión actual. Cualquier cambio,
   desaparición, descarga incompleta o fallo invalida toda la auditoría; no emitir
   un manifiesto completo ni continuar con resultados parciales.
4. Al terminar todos los objetos, volver a listar toda la raíz y comparar el conjunto
   de claves/tamaños; repetir HEAD actual de todas las claves y comparar VersionId
   con V0. Abortar ante altas, bajas, versiones distintas o inconsistencia. Mantener
   bloqueo hasta confirmar la evidencia; revalidar antes de cualquier futuro apply.
   VersionId antes/después ayuda a detectar concurrencia, pero no constituye una
   transacción global: sin exclusión efectiva de escritores puede haber cambios
   transitorios/restauraciones entre observaciones. No prometer detección absoluta
   sin bloqueo o control equivalente verificable de todos los escritores.
5. Solo tras éxito total emitir evidencia SHA256 con cuenta, bucket, claves, tamaños,
   versiones, tiempos y procedencia, y asociarla al manifiesto/diario. Conservar
   permisos mínimos GetObject y ListBucket para el recolector actual; GetObjectVersion
   se reserva para una recuperación futura. Verificar cifrado/KMS real antes de ampliar
   permisos. La implementación posterior está documentada en
   [S3-SHA256-AUDIT.md](S3-SHA256-AUDIT.md); no se ha ejecutado en producción.

Referencias oficiales para revisar la implementación futura:
[ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html),
[HeadObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html),
[GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html).

### Resultado local de Fase B

Rama: feat/optimize-aws-web-deploy. HEAD: 72ad37efaf16c796649c458c273e70984061fdb5.
Snapshot TEMP: vms-web-s3-inventory.json, 377455 bytes; SHA256 del archivo local:
2eddc685097898356dbd6ce670efdfea3e43756d4577f6dc6e301bc050c63bd2.
Este SHA identifica el archivo de metadatos; no es el SHA de los objetos S3.

Reporte completo: [deploy-metadata-report.json](deploy-metadata-report.json).
947 archivos Git, 947 entradas del snapshot, 941 coincidencias de clave/tamaño,
6 faltantes en el snapshot, 6 sobrantes y 0 diferencias de tamaño entre claves comunes.
Cobertura no demostrada: el snapshot omite IsTruncated. Plan bloqueado y sin evidencia
de igualdad de bytes. Las siguientes parejas solo facilitan la revisión humana;
el programa las conserva como claves independientes y no infiere renombres:

| Clave Git faltante en snapshot | Clave sobrante en snapshot | Bytes en cada lado |
| --- | --- | ---: |
| assets/img/Diseño_tecnico_infraestructura.png | assets/img/Dise±o_tecnico_infraestructura.png | 2248722 |
| assets/img/Especialidad_Mantenimiento_Diagnóstico_de_condición_criticidad.png | assets/img/Especialidad_Mantenimiento_Diagn¾stico_de_condici¾n_criticidad.png | 2419967 |
| assets/img/Especialidades_Capacitacion_Simulación_Software.png | assets/img/Especialidades_Capacitacion_Simulaci¾n_Software.png | 2428094 |
| assets/img/Projects/infraestructura_electrica/CFE_Chankanaab_Sistema_Eléctrico_Integral_CACD.png | assets/img/Projects/infraestructura_electrica/CFE_Chankanaab_Sistema_ElÚctrico_Integral_CACD.png | 2472649 |
| assets/img/carrousel_ingenería_experta.png | assets/img/carrousel_ingenerÝa_experta.png | 2384165 |
| assets/img/landings/hvac/originals/Instalación_puesta_pruebas_hvac.png | assets/img/landings/hvac/originals/Instalaci¾n_puesta_pruebas_hvac.png | 2264585 |

Hay diferencias de caracteres Unicode; falta determinar si proceden de las claves
remotas o de cómo se capturó/exportó el snapshot. No corregir la codificación ni borrar
claves basándose en esta hipótesis. Los tests cubren también claves Unicode distintas
y texto compuesto/descompuesto sin normalización automática.

Verificación ejecutada en Windows: `python -B -m unittest discover -s scripts -p
test_deploy_plan.py -v`: 15 pruebas aprobadas (2.841 s). `git diff --check` pasó;
sin diferencias en script productivo, workflow ni infra. No se ejecutaron operaciones
AWS ni descargas reales, apply, commit/push/merge en el repositorio. Los commits de
fixtures de tests se limitan a repositorios temporales aislados. Ubuntu sigue pendiente.
