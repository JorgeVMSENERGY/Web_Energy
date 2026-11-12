# Auditoría SHA-256 remota de solo lectura

Implementación: audit_s3.py. No se ha ejecutado contra AWS. El contexto confirmado
actual sustituye las discrepancias del snapshot histórico de Fase B: 947 claves y
tamaños coincidentes, sin diferencias; esto aún no comprueba igualdad de bytes.

## Ejecutar primero offline

```powershell
python -B -m unittest discover -s scripts -p 'test_*.py' -v
```

La simulación intercepta subprocess.run en la frontera de AWS CLI: procesa los JSON
de petición reales, devuelve páginas y HEAD/GET simulados y escribe el cuerpo binario
en el destino temporal. No invoca AWS ni necesita credenciales. Prueba hashes, binarios,
objetos vacíos, Unicode/espacios/+/#/?/% y percent-encoding, paginación, duplicados,
JSON inválido, longitudes parciales, drift de igual tamaño, altas/bajas, versiones
cambiantes antes/durante/después/final, permisos, timeout, interrupción, disco lleno,
HEAD Git cambiante, rutas fuera del repo y protección de reportes existentes.

Resultado ejecutado en Windows: **37 pruebas aprobadas en 3.613 s** (22 del recolector
y 15 del planificador previo). git diff --check pasó y no hay diferencias en el script
productivo, workflow ni infra. No se ejecutó el comando real ni se hicieron lecturas
AWS; no existe todavía un reporte de hashes productivos. Validación nativa Ubuntu y
compatibilidad de la CLI instalada con el servicio real quedan pendientes. Este
resultado histórico se amplía con la revisión de codificación/preflight descrita abajo.

## Preflight obligatorio antes de decidir una auditoría completa

Comando preparado, no ejecutado contra AWS:

```powershell
$PreflightReport = Join-Path $env:TEMP ('vms-web-preflight-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/audit_s3.py `
    --read-s3 --preflight `
    --repo . `
    --profile vms-web-infra `
    --expected-head 72ad37efaf16c796649c458c273e70984061fdb5 `
    --temp-root "$env:TEMP" `
    --output "$PreflightReport"
$PreflightExitCode = $LASTEXITCODE
Write-Host "Reporte: $PreflightReport; código: $PreflightExitCode"
```

Hace solo STS GetCallerIdentity y listado raíz paginado completo. La capa de CLI
prohíbe HEAD y GET de cuerpos en este modo. Debe encontrar exactamente 947 archivos
Git, 947 objetos S3 y 947 coincidencias exactas de clave/tamaño, sin faltantes,
sobrantes ni tamaños diferentes. Ante diferencias aborta con código 1 y conserva
el diagnóstico por clave en metadataComparison; no intenta arreglar nombres ni
continuar con descargas. Verifica de nuevo HEAD Git antes de declarar éxito.

Éxito: status=preflight-matches-git, preflightComplete=true, auditComplete=false,
objectsHashed=0 y applyBlocked=true. No es evidencia de bytes, versiones actuales,
SSE o ausencia de drift de igual tamaño. Con 947 objetos puede bastar un listado
de una página y una llamada STS, sin descargar los 574.7 MiB. No requiere GetObject,
solo ListBucket e identidad STS. Solo almacena metadatos y reporte fuera del repo.

Los modos --preflight y --audit son obligatorios y mutuamente excluyentes, además
de --read-s3. Omitir el modo ya no inicia implícitamente descargas. Un preflight
exitoso no ejecuta ni autoriza automáticamente la auditoría completa. La auditoría
completa sigue requiriendo un comando separado explícito; un preflight anterior no
es un bloqueo de escritores ni una instantánea transaccional.

## Revisión de codificación de claves

El decoder anterior usaba unquote_to_bytes y conservaba todo '+'. Eso interpretaba
incorrectamente la representación `CV+VMS+ENERGY+26_comprimido.pdf` observada para
una clave con espacios. La afirmación anterior de no convertir nunca '+' se corrige.

Se inspeccionó el código público de handlers y compat de botocore incluido en AWS CLI:
si el SDK agrega EncodingType=url implícitamente, activa encoding_type_auto_set y
decodifica la respuesta con unquote_str, que en esta versión es unquote_plus. Si la
petición trae EncodingType=url explícito, no aplica esa decodificación automática.
El campo EncodingType=url de una respuesta por sí solo no demuestra si sus claves
ya fueron decodificadas; importa cómo se construyó la petición.

Además se ejecutó la CLI instalada (aws-cli/2.36.42, Python/3.14.6 Windows/11) contra
un HTTPServer en 127.0.0.1 con XML fijo y --no-sign-request, configuración/credenciales
aisladas y metadata de EC2 deshabilitada. No se contactó S3, STS ni otro endpoint AWS.
La CLI conservó el texto codificado al pedir url explícitamente y lo decodificó al
omitir el parámetro. Se verificó tanto --encoding-type url como EncodingType en el
archivo --cli-input-json usado por el recolector. Esa comprobación está en
test_aws_cli_encoding.py; se omite si
no hay CLI instalada y debe ejecutarse antes de cambiar la versión de CLI utilizada.
Los handlers de la instalación distribuida están empaquetados, no disponibles como
fuentes .py locales; por eso se contrastó código público con este ensayo ejecutable.

| Texto en XML / JSON CLI con url explícito | Clave tras una decodificación | JSON CLI con url implícito |
| --- | --- | --- |
| CV+VMS+ENERGY+26_comprimido.pdf | CV VMS ENERGY 26_comprimido.pdf | CV VMS ENERGY 26_comprimido.pdf |
| literal%2B.pdf | literal+.pdf | literal+.pdf |
| literal%252B.pdf | literal%2B.pdf | literal%2B.pdf |
| a%20b.pdf | a b.pdf | a b.pdf |
| a%2520b.pdf | a%20b.pdf | a%20b.pdf |
| 100%25.pdf | 100%.pdf | 100%.pdf |
| 100%2525.pdf | 100%25.pdf | 100%25.pdf |
| %C3%B1.pdf | ñ.pdf | ñ.pdf |

El recolector siempre pide url explícito. Solo en esa frontera aplica unquote_plus
una vez con UTF-8 estricto, validando porcentajes y texto ASCII codificado. Así un '+'
codificado como %2B vuelve a '+' y no pasa después por otra conversión a espacio;
%252B queda como el texto literal %2B. No se decodifican claves Git, argumentos de
HEAD/GET ni tokens opacos. No hay heurísticas según coincidencias con Git, reemplazos
globales de nombres o normalización Unicode. Espacios crudos, controles, Unicode no
codificado y secuencias % inválidas en esta frontera fallan cerrados; duplicados tras
decodificar también abortan. El ensayo local comprueba la CLI y el fixture, no vuelve
a validar el comportamiento del servicio real ni la procedencia del snapshot previo.

Fuentes primarias:
[handlers de AWS CLI/botocore](https://github.com/aws/aws-cli/blob/v2/awscli/botocore/handlers.py),
[compat: unquote_str = unquote_plus](https://github.com/aws/aws-cli/blob/v2/awscli/botocore/compat.py).

Resultado de esta revisión en Windows: `python -B -m unittest discover -s scripts
-p 'test_*.py' -v` ejecutó **45 pruebas, todas aprobadas, en 10.721 s**, sin skips.
Incluye el ensayo real de CLI contra localhost con tres peticiones (url explícito
por flag, explícito por archivo JSON e implícito), y pruebas de 947 coincidencias,
diferencias, colisiones tras decodificar y bloqueo de HEAD/GET en preflight.
git diff --check pasó; script/workflow productivos e infra permanecen sin cambios.
No se ejecutó preflight ni auditoría contra AWS, ni commit, push o merge.

## Comando real preparado, NO ejecutado

Desde C:\web-dev\projects\vms-energy-web, con Python 3.8+ y AWS CLI v2 instalado:

```powershell
$AuditReport = Join-Path $env:TEMP ('vms-web-sha256-audit-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/audit_s3.py `
    --read-s3 --audit `
    --repo . `
    --profile vms-web-infra `
    --expected-head 72ad37efaf16c796649c458c273e70984061fdb5 `
    --temp-root "$env:TEMP" `
    --output "$AuditReport"
$AuditExitCode = $LASTEXITCODE
Write-Host "Reporte: $AuditReport; código: $AuditExitCode"
```

`--read-s3` es obligatorio; sin él no hay llamadas AWS. No existe modo de escritura.
Cuenta 787140332697, bucket vmsenergy-web-prod-787140332697 y endpoints AWS oficiales
us-east-1 están fijados. STS verifica Account y cuenta en ARN, y cada llamada S3 lleva
ExpectedBucketOwner. No se permite cambiar bucket/cuenta por parámetros ni solicitar
VersionId histórico. El HEAD Git debe coincidir con el commit fijado antes y al final.
El inventario de blobs HEAD se calcula otra vez, sin depender del checkout ni del
reporte histórico. No requiere árbol limpio: cambios locales no alteran los blobs
auditados. La rama de trabajo prevista es feat/optimize-aws-web-deploy.

## Lecturas y algoritmo

1. STS GetCallerIdentity, sin llamadas de escritura. ListObjectsV2 manual con
   --no-paginate, MaxKeys=1000, todas las páginas hasta IsTruncated=false, validando
   tokens, KeyCount, nombre del bucket, tamaño y unicidad dentro/entre páginas.
2. Se pide EncodingType=url explícitamente y se decodifica una sola vez como UTF-8
   estricto con unquote_plus, distinguiendo '+' de %2B sin normalizar Unicode. ContinuationToken
   es opaco y no se decodifica. Las peticiones viajan como JSON UTF-8 con escapes
   Unicode, sin shell ni redirecciones de texto. No se reutiliza la antigua captura
   de metadatos para la auditoría. Claves incompatibles con la política de rutas del
   planificador (p. ej. '..', controles o marcadores de directorio) abortan, no se omiten.
3. Para cada clave actual: HEAD actual registra V0 y tamaño; exige VersionId válido,
   ausencia de delete marker y SSE-S3 AES256. GET actual **sin VersionId**, sin Range
   y sin transformaciones, devuelve VG. Se exige VG=V0, longitud de respuesta y de
   archivo igual a la listada; se calcula SHA256 leyendo bloques de 1 MiB. Se compara
   con tamaño y SHA256 del blob Git. No se interpretan ETag ni CRC64NVME como SHA256.
4. Se elimina el cuerpo descargado antes de pasar al siguiente objeto. Otro HEAD
   actual debe devolver V1=VG=V0. Se repite la enumeración completa, comparando claves
   y tamaños contra la inicial, y luego un HEAD actual final de cada objeto exige
   Vfinal=V0. Así también se detecta drift que aparece después del control individual.
5. Solo después del éxito total se marca auditComplete=true. Un drift estable respecto
   a Git produce differences-found (código 2), no una afirmación de igualdad. Un cambio
   de versión/clave/tamaño durante la auditoría, error de permisos/red/CLI/JSON,
   descarga incompleta o interrupción invalida toda la auditoría y aborta más lecturas.

No se presupone una transacción global de S3. El control antes/después no detecta todo
cambio transitorio, restauración de una versión anterior o modificación posterior al
último HEAD. Coordinar y controlar todos los escritores (Actions y operadores) durante
la auditoría; no basta el bloqueo del workflow. El reporte declara esta limitación,
no afirma atomicidad ni que se haya comprobado un bloqueo externo.

## Reporte y fallos

JSON local con identidad, bucket, commit, tiempos, llamadas CLI, conteos y registro
por clave: tamaño/hash Git, tamaño S3, SHA256 calculado, longitud descargada, VersionId
antes/GET/después/final y estado match/drift/missing-in-s3/extra-in-s3/pending.
Una clave faltante o sobrante se informa, sin crear operaciones de escritura.
Los datos parciales de un aborto son diagnósticos y deben descartarse como evidencia.

Siempre applyBlocked=true. Intencionadamente no exporta el contrato files/complete/
verification que desbloquea el planificador: ni siquiera un reporte coincidente es
un manifiesto confirmado ni una autorización de publicación. No se modifica deploy_plan.py.

Códigos: 0 auditoría estable y coincidente; 2 diferencias estables; 1 aborto/error;
130 interrupción Ctrl+C. Errores de argumentos también pueden usar código 2 y no
generar reporte: interpretar siempre status/auditComplete, no solo el código.
Un reporte existente nunca se sobrescribe. Se reserva un reporte in-progress antes
de comenzar; al terminar o ante excepción/Ctrl+C se reemplaza atómicamente por JSON
final. La escritura final fallida deja el reporte incompleto y el proceso falla.
No se incluyen stderr arbitrario ni credenciales en el reporte.

Los temporales se crean en un directorio privado aleatorio fuera del repositorio;
los nombres de objetos nunca se usan como rutas locales. Se limpia cada descarga
en finally, incluso si falla GET, y todo el directorio al salir normalmente o por
excepción/Ctrl+C. Una terminación forzada del proceso/host puede impedir finally:
el reporte queda in-progress, nunca se debe aceptar, y puede requerir limpieza manual
del directorio TEMP vms-s3-audit-* perteneciente a esa ejecución. No hay reanudación
implícita: repetir con reporte nuevo y volver a verificar todo.

## Permisos, espacio y costos previstos

Solo STS GetCallerIdentity y S3 ListBucket/GetObject (HEAD y GET actuales). No se
requiere GetObjectVersion porque no se envía versionId, ni ListBucketVersions,
GetBucketVersioning, escritura, IAM, Terraform o CloudFront. Se valida SSE-S3 en cada
HEAD/GET sin leer o modificar la configuración del bucket. No se pide checksum-mode.

Para 947 objetos y páginas completas: 947 GET de cuerpos, 2841 HEAD (3 por objeto),
2 ListObjectsV2 y 1 STS; unas 3790 lecturas S3. Se registran invocaciones CLI, no un
contador facturado del servidor. Retries del cliente se limitan a un intento; redirects,
autenticación y fallos pueden alterar solicitudes reales y costos.

Volumen previsto según el contexto confirmado: unos 574.7 MiB descargados más
metadatos. Transferencia por Internet y solicitudes GET/HEAD/LIST pueden tener costo;
dependen de región, destino, clase, tarifas y franquicias aplicables. No se presupone
gratuidad ni se calcula un importe sin esos datos. Repetir la auditoría vuelve a
descargar los cuerpos y puede repetir cargos. No se generan cargos de invalidación.

No hace falta guardar 574.7 MiB simultáneamente: un solo cuerpo a la vez. El inventario
Git local del commit fijado tiene un máximo de 19097995 bytes (18.21 MiB); si S3 sigue
coincidiendo en tamaño, esa es la mayor descarga. El código exige antes de cada GET
espacio libre para tamaño del objeto + 16 MiB; reservar al menos **64 MiB libres** en
TEMP para este inventario, más margen si otros procesos lo usan. El reporte y los JSON
también ocupan disco. Si los tamaños cambian, manda el mayor objeto real; no se cargan
todos los cuerpos en memoria, solo metadatos/hash y bloques de lectura de 1 MiB.

Referencias oficiales:
[GetObject y permisos actuales frente a versiones](https://docs.aws.amazon.com/cli/latest/reference/s3api/get-object.html),
[HEAD](https://docs.aws.amazon.com/cli/latest/reference/s3api/head-object.html),
[paginación y codificación de ListObjectsV2](https://docs.aws.amazon.com/cli/latest/reference/s3api/list-objects-v2.html),
[precios S3](https://aws.amazon.com/s3/pricing/).


## Correccion de GetObject exit 252 (2026-11-10)

El checkout recibido ya contenia la correccion candidata: GetObject usa flags
--bucket, --key y --expected-bucket-owner, con outfile posicional. Esta revision
la verifico con AWS CLI 2.36.42 real contra HTTP en 127.0.0.1, sin llamadas AWS.

Causa reproducida: el comando streaming GetObject no admite --cli-input-json.
La forma anterior (campos API solo en JSON + outfile) devuelve 252 e informa
que faltan --bucket y --key. Incluso agregando esos flags, mantener
--cli-input-json devuelve 252 con Unknown options. Ambos fallan antes del HTTP.
No es evidencia de un fallo de permisos S3. El JSON de API no puede aportar
outfile: este es un argumento posicional exclusivo de CLI y es obligatorio.

--output json formatea los metadatos en stdout; no selecciona ni escribe el
cuerpo. --query filtra esos metadatos y tampoco sustituye outfile. El recolector
no usa --query para conservar VersionId, ContentLength y ServerSideEncryption.
La integracion verifica tambien una consulta de metadatos sin alterar bytes.

La prueba test_get_object_collector_argv_outfile_and_unsupported_json_regression
invoca AwsCli.call, el mismo constructor usado por collect. Solo sustituye
endpoint, autenticacion y entorno al ejecutar la CLI instalada. Exige 2.36.42,
aisla configuracion/credenciales y deshabilita metadata EC2. Comprueba binarios,
VersionId, ContentLength, SHA256, claves especiales y regresiones de sintaxis.
Ademas ejecuta audit/collect con HEAD y GET reales locales e identidad/listado
simulados: exito, ContentLength incorrecto y retorno 252 previamente reproducido.
Comprueba limpieza del payload/directorio temporal, auditComplete=false y
applyBlocked=true ante fallos. El retorno 252 del ciclo completo es inyectado;
la reproduccion de sintaxis anterior si ejecuta la CLI real.

Verificacion segura, sin lecturas AWS ni los 947 GET:

```powershell
python -B -m unittest discover -s scripts -p 'test_*.py' -v
python -B -m unittest discover -s infra/forms/tests -p 'test_*.py' -v
```

No se modificaron deploy-aws-production.ps1, deploy-production.yml, Terraform,
IAM ni CloudFront. No hubo cargas, eliminaciones, invalidaciones, commit,
push ni merge. La prueba manual productiva aportada por el usuario es contexto;
Codex no la repitio ni ejecuto la auditoria productiva.


## Backend Boto3 opcional y concurrencia (2026-11-10)

Evidencia aportada por el operador: 94 hashes correctos, 0 diferencias, 853
pendientes, 760 segundos, 94 GET + 189 HEAD, KeyboardInterrupt. Ese reporte sigue
siendo incompleto y no se importa ni se usa para reanudar esta implementacion.
Toda ejecucion vuelve a inventariar Git/S3 y crea un reporte nuevo fuera del repo.

Costo observado: 8.09 s/objeto, o 2.69 s por invocacion HEAD/GET contando todo el
tiempo observado. Esto no mide por separado arranque, SSO, TLS, red y transferencia.
La CLI reinicia Python/cliente/proveedor de credenciales en cada lectura y no
comparte conexiones entre procesos. Ademas escribe cada cuerpo a disco y lo vuelve
a leer para SHA256. El algoritmo secuencial tampoco solapa latencia. Todavia
faltaba el barrido final completo: extrapolar solo 8.09 s/objeto subestima el total.
No se promete una aceleracion concreta sin medir el backend en el entorno real.

El backend CLI sigue siendo el predeterminado, con un solo trabajador. Boto3 crea
una Session con el perfil elegido, y clientes S3/STS una sola vez en el hilo
principal. Solo los clientes se comparten entre hilos. Conserva el proveedor
renovable del perfil (incluido SSO), sin congelar/exportar credenciales. Reutiliza
el pool HTTP (tamano igual a workers), con region us-east-1 y endpoints oficiales
explicitos; ignora endpoints alternativos configurados. Cuenta verificada por STS,
bucket y ExpectedBucketOwner siguen fijos. La cache SSO debe estar vigente;
si requiere renovar el login interactivo, hacerlo antes desde la terminal del
operador. No hay login automatico ni fallback a otro perfil. Los proveedores SSO
pueden renovar credenciales mediante sus endpoints de identidad durante una
lectura real; no son llamadas de escritura a S3.

Boto3 lee bloques de 1 MiB, calcula SHA256 sin archivo de cuerpo temporal y cierra
StreamingBody siempre, incluso ante cancelacion, error de metadatos o interrupcion.
Valida HEAD(V0), metadatos GET(VG=V0), ContentLength y bytes leidos, HEAD(V1=V0).
En modo completo conserva el listado final de todas las claves/tamanos y HEAD final
de TODOS los objetos (Vfinal=V0), mas la comprobacion final del commit Git.
Unicode y codificacion url explicita se procesan exactamente una vez por el mismo
validador del recolector. Se mantiene SSE-S3 AES256 y no se solicita VersionId
historico, Range ni checksum alternativo.

--workers admite 1..4 (default 1); mas de uno requiere --backend boto3. Hay como
maximo workers tareas pendientes; no se encolan los 947 objetos. Un fallo activa
cancelacion, impide nuevas admisiones HTTP y nuevas tareas, cancela las pendientes
y espera a las ya iniciadas antes de cerrar clientes y escribir el reporte.
Solicitudes ya admitidas pueden terminar; streams comprueban cancelacion entre
bloques. Una lectura bloqueada puede tardar hasta el timeout de lectura (60 s)
en devolver control. Retries limitados a un intento. No se afirma cancelacion
instantanea de sockets ni transaccion global: controlar escritores sigue siendo
necesario. El barrido final de HEAD se mantiene secuencial sobre el cliente persistente.

--max-objects N elige las primeras N claves ordenadas como muestra diagnostica.
Lista el bucket completo al inicio y al final, pero GET y comprobaciones de version
solo se hacen para la muestra. Conserva auditComplete=false incluso si N supera
el total. status=sample-verified (exit 0) o sample-differences (exit 2); applyBlocked
siempre true. La muestra no demuestra igualdad de los objetos pendientes y no
se combina con --preflight. Sin limite, solo una auditoria completa estable puede
marcar auditComplete=true; incluso entonces applyBlocked=true. No hay argumento
de entrada de reportes ni reanudacion de los 94 hashes previos.

Dependencia opcional probada en el Python 3.8 local: boto3==1.37.38,
botocore==1.37.38 (resuelto por boto3), s3transfer==0.11.5. El fichero
requirements-audit.txt fija Boto3; pip resuelve sus dependencias compatibles.
Para instalar en el entorno Python del operador:

```powershell
python -m pip install -r scripts/requirements-audit.txt
python -B -m unittest discover -s scripts -p 'test_*.py' -v
python -B -m unittest discover -s infra/forms/tests -p 'test_*.py' -v
```

Las pruebas Boto3 usan credenciales ficticias y servidor HTTP 127.0.0.1, conservan
el transporte/parser SDK reales, y simulan STS. Verifican conexiones HTTP/1.1
reutilizadas, binarios multibloque/vacios, Unicode, versiones GET/post/final,
listado final cambiante, drift, 403, cancelacion con ventana de 4 tareas, validadores,
streams cortos/largos, errores/interrupciones y cierre. Se omiten si falta Boto3;
instalar la dependencia es necesario para ejecutar la verificacion completa.
Codex instalo el SDK solo en un directorio temporal de pruebas, fuera del proyecto.

Comandos preparados para el operador, NO ejecutados contra AWS:

```powershell
# Muestra inicial: como maximo 4 GET; reporte siempre incompleto.
$SampleReport = Join-Path $env:TEMP ('vms-sample-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/audit_s3.py --read-s3 --audit --backend boto3 --workers 4 --max-objects 4 --repo . --profile vms-web-infra --temp-root "$env:TEMP" --output "$SampleReport"

# Auditoria completa nueva, solo despues de revisar la muestra.
$FullReport = Join-Path $env:TEMP ('vms-full-' + [guid]::NewGuid().ToString('N') + '.json')
python -B scripts/audit_s3.py --read-s3 --audit --backend boto3 --workers 4 --repo . --profile vms-web-infra --temp-root "$env:TEMP" --output "$FullReport"
```

No se ejecutaron lecturas AWS reales, escrituras, invalidaciones ni cambios de
workflow/despliegue, IAM, Terraform o CloudFront. Sin commit, push ni merge.

Referencias: https://docs.aws.amazon.com/boto3/latest/guide/credentials.html
https://boto3.amazonaws.com/v1/documentation/api/1.18.47/guide/clients.html
https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html

Resultado final offline: 58 pruebas de scripts aprobadas en 43.767 s,
sin skips, incluidas 11 pruebas nuevas Boto3; 23 pruebas de formularios
aprobadas en 0.210 s. Total: 81 aprobadas. git diff --check paso y no
hay cambios en archivos rastreados productivos. Los archivos del auditor
y documentacion ya estaban sin rastrear; esta revision no los agrega a Git.


## Diagnosticos de rendimiento (2026-11-10)

Esta revision agrega diagnostics.performance, sin usarlo para decidir estados,
autorizar apply ni omitir validaciones. Son duraciones monotonicamente medidas
con time.perf_counter, en segundos. phases contiene count, failed, totalSeconds
y maxSeconds; objects contiene el desglose para cada clave ya presente en el
reporte. No se guardan argumentos del SDK, respuestas, errores arbitrarios,
credenciales, tokens, URLs firmadas ni trazas HTTP. Los nombres de fase son fijos.
Las mediciones tambien se conservan ante excepciones y KeyboardInterrupt.

Fases principales:

- git.root / git.head: resolucion del repositorio y verificaciones inicial/final.
- git.inventory: inventario completo, incluso en una muestra de 4 objetos.
  Desglose git.rev-parse, git.ls-tree, git.cat-file y git.sha256. El argumento
  performance opcional de deploy_plan.inventory no cambia comandos ni blobs.
- boto3.initialization, boto3.imports, boto3.session, boto3.client.s3/sts:
  importacion, construccion de sesion, clientes y resolucion diferida del perfil.
- sdk.signing_credentials: before-sign hasta before-send en las llamadas que ya
  existian. Incluye firma y, si ocurre ahi, renovacion/SSO. No distingue tiempo SSO
  puro ni obliga a resolver/congelar credenciales antes. Un fallo de firma conserva
  el tiempo parcial. No agrega llamadas de autenticacion ni red.
- sts.identity y sdk.get-caller-identity: llamada de identidad.
- listing.initial/final: wall inclusive de cada listado, con .request para cada
  pagina y .decode_validate para decodificar/validar/agrupar claves. El tiempo
  .request incluye transporte y parsing del SDK. inventory.* mide comparaciones
  de registros, preflight, resultado por objeto y comparacion del listado final.
- head.initial / head.after / head.final: tiempos por objeto y agregados, con
  fases de validacion separadas. head.final_batch_wall mide el barrido final.
- get.total, get.response_headers, get.first_chunk_upper_bound: tiempo hasta
  devolver respuesta del SDK/cabeceras y hasta disponer del primer bloque no vacio.
  NO son TTFB exacto de socket: son limites superiores observables por la aplicacion.
  Se mantiene body.read(1 MiB), sin introducir una lectura adicional de un byte.
  get.empty_body_eof se usa para cuerpos vacios; no se inventa primer byte.
- get.streaming incluye lecturas y computo; get.body_read mide solo las llamadas
  read (incluyendo EOF), get.sha256 solo update/hexdigest, get.size_validation
  comprueba bytes acumulados/finales. get.metadata_validation y get.body_close
  separan validacion de metadatos y cierre. Los SDK GET solo miden hasta cabeceras.
- objects.batch_wall mide la fase concurrente completa, incluido esperar/cancelar.
- backend.close y report.initial_serialization_write registran cierre/reserva.
- report.payload_serialization y report.payload_write_flush_fsync miden serializar
  y escribir/flush/fsync del cuerpo del reporte. Se agrega diagnostics al final
  del MISMO temporal, luego fsync final y reemplazo atomico. La cola diagnostica
  y fsync/replace finales quedan fuera de las duraciones almacenadas: un archivo
  no puede contener el tiempo exacto de su propio reemplazo sin otra escritura.
  elapsedBeforeReportSeconds mide el wall desde entrar en audit hasta guardar;
  excluye arranque Python, imports previos, cola diagnostica y salida de consola.

Duraciones inclusivas y trabajadores solapados: NO sumar todas las fases para
comparar con PowerShell. Comparar primero git.inventory, boto3.initialization,
sts.identity, listing.initial, objects.batch_wall, listing.final y
head.final_batch_wall. Profundizar despues en la fase dominante. sdk.* y
get.streaming contienen subfases; sus sumas no son wall de la auditoria.

Medicion local SIN AWS, solo inventario Git actual de 947 archivos / 602626178
bytes, con el constructor existente de subprocess, durante pruebas locales:

| Fase | Llamadas | Segundos |
| --- | ---: | ---: |
| git.inventory | 1 | 151.2935175 |
| git.cat-file | 947 | 149.7932494 |
| git.sha256 | 947 | 0.7465793 |
| git.rev-parse | 1 | 0.2092380 |
| git.ls-tree | 1 | 0.2699340 |

El inventario completo es un costo fijo tambien en --max-objects 4. Esta corrida
sustenta a los subprocess Git como causa probable importante de la demora de la
muestra, no a S3. No atribuye exactamente los 127.7238931 s del operador: son
corridas distintas y esta medicion coincidio con pruebas concurrentes. Para
atribuir los 127 s se necesita el nuevo reporte de ESA ejecucion. No se modifica
el inventario para optimizarlo en esta tarea; solo se mide su comportamiento actual.

Nueva muestra preparada, NO ejecutada contra AWS por Codex:

```powershell
$SampleReport = Join-Path $env:TEMP ('vms-perf-sample-' + [guid]::NewGuid().ToString('N') + '.json')
$SampleClock = [System.Diagnostics.Stopwatch]::StartNew()
python -B scripts/audit_s3.py --read-s3 --audit --backend boto3 --workers 4 --max-objects 4 --repo . --profile vms-web-infra --temp-root "$env:TEMP" --output "$SampleReport"
$SampleExit = $LASTEXITCODE
$SampleClock.Stop()
Write-Host "Reporte: $SampleReport; exit: $SampleExit; wall: $($SampleClock.Elapsed.TotalSeconds) s"
$SamplePerf = (Get-Content -LiteralPath $SampleReport -Raw | ConvertFrom-Json).diagnostics.performance
$SamplePerf.phases | ConvertTo-Json -Depth 8
```

Mantiene como maximo cuatro GET; enumera todo al principio/final y hace HEAD
inicial/posterior/final para la muestra. No cambia limites, timeout, retries,
checksums, streaming, versiones, estados, applyBlocked ni politica de credenciales.
El reporte de muestra conserva auditComplete=false y applyBlocked=true.

Validacion final de esta instrumentacion: 69 pruebas de scripts aprobadas
en 54.837 s, sin skips, mas 23 pruebas de formularios aprobadas en 0.415 s.
Total: 92 (las 81 existentes y 11 nuevas). git diff --check, sintaxis y
whitespace de archivos nuevos pasaron. No se ejecuto la muestra real.


## Inventario Git con cat-file --batch (2026-11-10)

La referencia completa del algoritmo anterior se genero y guardo ANTES de
sustituir inventory. No hubo pruebas concurrentes durante ninguna de las dos
mediciones. Ambas corridas leyeron el commit
72ad37efaf16c796649c458c273e70984061fdb5, 947 claves y 602626178 bytes.
La comparacion de los diccionarios schema/commit/files fue exacta: las 947 claves,
los 947 tamanos y los 947 SHA256 coinciden; cero diferencias. Ademas ambos JSON
ordenados guardados son identicos byte a byte, SHA256 del manifiesto completo:
95ad406dcc7198bfaf0e5b619e37cd601bec14c403bcf50ca38ac745a1e7b28b.
Este hash identifica el manifiesto, no un blob individual.

| Inventario completo | Segundos | Procesos Git | Procesos cat-file |
| --- | ---: | ---: | ---: |
| Anterior, referencia nueva aislada | 168.5287469 | 949 | 947 |
| Nuevo, batch persistente | 15.0411830 | 4 | 1 |

Aceleracion observada del inventario: 11.20 veces. Los 151.29 s anteriores se
conservan como evidencia historica, no se mezclan con esta comparacion aislada.
Los tiempos incluyen la misma instrumentacion opcional de performance y contador
de Popen; la medicion nueva incluye ademas la comprobacion final de HEAD.
Los procesos de este cuadro son solo los internos de inventory, sin los controles
HEAD adicionales del auditor o los dos controles externos de la referencia.
No son una promesa de tiempo para la auditoria completa o transferencia AWS.

Artefactos de referencia fuera del repositorio (temporales conservados):
C:/Users/JORGEP~1/AppData/Local/Temp/vms-git-batch-reference-4a2wk2k7/
Contiene deploy_plan_before.py (fuente anterior guardada antes del cambio),
reference.json, baseline.json, batch.json y comparison.json. comparison.json
registra commit, igualdad, diferencias vacias, tiempos, procesos y hashes de los
manifiestos. No contiene cuerpos descargados ni datos de credenciales.

Implementacion compartida: deploy_plan.inventory es la unica construccion de
inventario usada por planificador y auditor. git_batch.GitBatch implementa solo
el protocolo de lectura. inventory resuelve HEAD una vez, enumera con
ls-tree -rzl --full-tree COMMIT (OID y tamano de cada blob), y pide esos OID por
stdin binario a un unico git cat-file --batch. No usa rutas en las solicitudes,
archivos del checkout, filtros, textconv ni git archive. Preserva los bytes de
los blobs, incluidos vacios, binarios y finales de linea; los nombres siguen
UTF-8 estricto y la misma politica safe_key/publishable existente.

Cada cabecera esta limitada a 256 bytes y debe contener exactamente el OID pedido,
tipo blob y tamano decimal igual al enumerado en el arbol. El cuerpo se lee en
bloques de como maximo 1 MiB, admitiendo lecturas parciales; SHA256 se actualiza
incrementalmente. Se exige el separador LF del protocolo, salida Git cero y EOF
sin bytes adicionales al cerrar stdin. La memoria del inventario conserva solo
metadatos/hash y un bloque de cuerpo, nunca todos los 602 MB ni un blob completo.
Errores, missing, tipo/OID/tamano/separador incorrectos, truncamientos, errores de
pipe, timeout de salida o KeyboardInterrupt invalidan el resultado y cierran/recolectan
el proceso persistente. stderr del proceso batch no se incorpora a reportes.

Se vuelve a resolver HEAD antes de devolver el inventario: si cambio respecto al
inicio se aborta. La lectura siempre esta fijada al commit resuelto inicialmente,
aunque el working tree se modifique. Un ref explicito distinto de HEAD sigue
siendo compatible y necesita un proceso rev-parse adicional (5 en vez de 4).
Los controles HEAD antes/despues no son una transaccion ni detectan todo cambio
transitorio que vuelva al mismo commit; el contenido leido sigue fijado por OID.

Contrato JSON de inventory SIN cambios: schema=1, commit, files con size/sha256.
No se agregan campos de autorizacion. El auditor sigue applyBlocked=true y
mantiene todos sus controles de S3. Sus fallos de inventario guardan reporte
incompleto y abortan antes de construir el transporte AWS. No hay fallback al
checkout ni a evidencia parcial/anterior. No se agrega dependencia externa.

Compatibilidad de diagnostics.performance: estructura de fases identica.
git.cat-file ahora cuenta 1 proceso persistente y mide su ciclo completo,
incluido streaming/hash. git.rev-parse cuenta 2 para HEAD (inicio/final).
Se agregan git.batch_start/header/read/blob/finish. git.sha256 cuenta updates
por bloque y hexdigest, no blobs; sus duraciones siguen siendo computo SHA256.
No sumar fases inclusivas como si fueran tiempos de pared independientes.

La muestra Boto3 se repite con el mismo comando y un reporte NUEVO:

```powershell
$SampleReport = Join-Path $env:TEMP ('vms-batch-sample-' + [guid]::NewGuid().ToString('N') + '.json')
$SampleClock = [System.Diagnostics.Stopwatch]::StartNew()
python -B scripts/audit_s3.py --read-s3 --audit --backend boto3 --workers 4 --max-objects 4 --repo . --profile vms-web-infra --temp-root "$env:TEMP" --output "$SampleReport"
$SampleExit = $LASTEXITCODE
$SampleClock.Stop()
Write-Host "Reporte: $SampleReport; exit: $SampleExit; wall: $($SampleClock.Elapsed.TotalSeconds) s"
(Get-Content -LiteralPath $SampleReport -Raw | ConvertFrom-Json).diagnostics.performance | ConvertTo-Json -Depth 10
```

Comando preparado, NO ejecutado por Codex contra AWS. La muestra sigue limitada
a 4 GET, auditComplete=false y applyBlocked=true. No se reutilizan hashes de
reportes interrumpidos. Sin uploads, deletes, invalidaciones ni cambios de
workflow/despliegue, IAM, Terraform, S3 o CloudFront. Sin commit, push ni merge
en el proyecto. Los repositorios de fixtures de tests son temporales aislados.

Validacion final batch: 80 pruebas de scripts aprobadas en 65.527 s,
sin skips; 23 pruebas de formularios aprobadas en 0.310 s. Total 103: se
conservan las 92 anteriores y se agregan 11 regresiones batch. La prueba
de instrumentacion existente adapta su fixture al protocolo batch y sigue
comprobando identidad de comandos/bytes con y sin diagnosticos.
