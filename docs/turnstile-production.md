# Turnstile: estado productivo y operación

## Estado validado

Staging y producción tienen Turnstile activo. Este documento registra el estado
operativo comunicado para el cierre de `release/turnstile-infra-prod`, con base
`origin/main`; los archivos técnicos fueron recuperados desde `origin/staging`.
Las comprobaciones AWS y de navegador indicadas abajo fueron realizadas antes de
esta actualización documental; esta revisión no consulta ni modifica AWS.

| Ambiente | Sitekey pública | Hostnames exactos | Activo |
| --- | --- | --- | --- |
| staging | `0x4AAAAAAF1Ixp8IY8H5Laik` | `jorgevmsenergy.github.io` | Sí |
| prod | `0x4AAAAAAF1Uw3YVAufKwYit` | `vmsenergy.com`, `www.vmsenergy.com` | Sí |

La Lambda productiva es `vmsenergy-forms-prod-receiver`, con
`TURNSTILE_ENABLED=true`, estado `Active` y `LastUpdateStatus=Successful`.
Secrets Manager e IAM están configurados y operativos. La activación productiva
ya está completada; no quedan pasos de aprovisionamiento inicial en este documento.

## Arquitectura y comportamiento

El cliente obtiene un token para cada intento y lo envía como `turnstile_token`
a `/submissions`, con una acción correspondiente al `form_type`. El widget Managed
usa apariencia `interaction-only`. Los adjuntos se suben antes de obtener el token.

El receptor lee el SecretString de su ambiente desde Secrets Manager y consulta
Siteverify por HTTPS, con timeout de 5 segundos. Exige `success is True`, hostname
incluido exactamente en la lista permitida y acción igual al tipo de formulario.
No acepta sufijos de dominio ni usa Origin o `page_url` como prueba de autenticidad.
`TURNSTILE_HOSTNAMES` contiene la lista JSON; la variable singular heredada
`TURNSTILE_HOSTNAME` solo se usa si la plural está ausente. Una lista plural vacía
o malformada no utiliza ese fallback.

La verificación ocurre antes de finalizar adjuntos, escribir el lead en DynamoDB
y enviar a SQS para las notificaciones. Los tokens no se guardan en esos destinos.
Una solicitud por lo demás válida sin token devuelve HTTP 400
`turnstile_required`; un token rechazado, hostname o acción incorrectos devuelve
400 `turnstile_invalid`. Fallos de secreto, configuración, red o proveedor devuelven
503 `turnstile_unavailable`, sin permitir el envío ni exponer detalles sensibles.
Cloudflare rechaza tokens caducados o reutilizados.

`/uploads/presign` conserva su comportamiento previo y no consume tokens Turnstile.
El flag servidor desactivado omite la verificación: es un bypass de protección que
solo debe usarse como parte de un rollback autorizado.

## Secrets Manager y permisos efectivos

| Ambiente | ARN del secreto |
| --- | --- |
| prod | `arn:aws:secretsmanager:us-east-1:787140332697:secret:vmsenergy/forms/prod/turnstile-q8Vt0E` |
| staging | `arn:aws:secretsmanager:us-east-1:787140332697:secret:vmsenergy/forms/staging/turnstile-kRofj2` |

Terraform administra únicamente los metadatos del secreto, independientemente del
flag de activación. El valor es un SecretString crudo administrado fuera de
Terraform; no se crean versiones ni se leen valores como data sources. No incluir
Secret Keys o tokens en Git, comandos, variables Lambda, logs ni documentación.
Los ARN y las sitekeys de este documento son referencias, no valores privados.
El código lee el secreto en cada verificación con timeouts AWS de 2 segundos y
un único intento. Rotar su valor no requiere publicar código; debe coordinarse con
la configuración de Cloudflare. Se utiliza el cifrado administrado de Secrets Manager.

La permissions boundary vigente es **VMSWebFormsExecutionBoundary v3**. Conserva
las siete declaraciones previas y agrega `ReadProductionTurnstileSecret`, con
`secretsmanager:GetSecretValue` como única acción sobre el ARN productivo exacto.
La declaración `ReadStagingTurnstileSecret` conserva su patrón
`arn:aws:secretsmanager:us-east-1:787140332697:secret:vmsenergy/forms/staging/turnstile-??????`.
Los seis signos `?` representan el sufijo de seis caracteres asignado por AWS.

La boundary limita permisos; no los concede. La policy de cada receptor concede
GetSecretValue sobre el ARN exacto de su propio secreto; el notifier no recibe
este permiso. La simulación IAM del rol receptor productivo mostró:

| Recurso consultado por el rol productivo | Resultado |
| --- | --- |
| Secreto prod | `allowed` |
| Secreto staging | `implicitDeny` |

El resultado staging corresponde al rol productivo y confirma su aislamiento;
no significa que el receptor staging esté impedido de leer su propio secreto.

`infra/forms/turnstile-boundary-prod.statement.json` y
`infra/forms/turnstile-boundary-staging.statement.json` son **declaraciones de
referencia de la boundary, NO documentos de policy completos para aplicar**.
No reemplazar la boundary con estos archivos: carecen del documento completo y de
sus demás declaraciones. El Sid de la policy del receptor es independiente de
los Sid de estas referencias. La boundary no está administrada por este módulo
Terraform. Cualquier cambio futuro debe preservar sus demás permisos y revisar
policies del rol, SCP, resource policies y denegaciones explícitas.

## Evidencia y validación

Se reportaron como exitosas las siguientes comprobaciones reales:

- Staging activo y validado.
- Formularios productivos legítimos en `vmsenergy.com` y `www.vmsenergy.com`.
- Notificaciones recibidas correctamente.
- API productiva: solicitud sin token con respuesta HTTP 400 `turnstile_required`.
- Lambda productiva activa y actualización exitosa; simulación IAM indicada arriba.

La suite local simula AWS y Siteverify; cubre hostnames de ambos ambientes,
acciones, tokens ausentes/inválidos/reutilizados, fallos del secreto/proveedor,
configuración incorrecta y ausencia de efectos persistentes cuando se rechaza.
Los casos con el flag desactivado comprueban ese comportamiento del código y no
describen el estado desplegado. Estas pruebas no certifican permisos AWS reales
ni sustituyen las pruebas de navegador. No se afirma que todos los casos negativos
de la suite se hayan ejecutado contra producción.

Ejecutar desde la raíz, sin aplicar cambios:

```powershell
python -B -m unittest discover -s infra/forms/tests -v
terraform -chdir=infra/forms fmt -check
terraform -chdir=infra/forms validate
git diff --check
```

## Operación y riesgos

Ante incidentes, revisar estado de Lambda, tasas de 400/503, recepción de
notificaciones, configuración de hostnames y permisos del receptor. No registrar
payloads de Siteverify, SecretString ni tokens. Un 400 por token ausente puede
corresponder a una pestaña antigua o un script bloqueado; solicitar recarga y
comprobar un envío controlado en cada hostname. Un 503 requiere revisar proveedor,
configuración y acceso al secreto, manteniendo el rechazo seguro.

Cloudflare y Secrets Manager añaden latencia y dependencia de disponibilidad;
su fallo impide envíos. Presign conserva su superficie pública previa; archivos
subidos antes de un rechazo quedan sujetos a la limpieza existente. El patrón
staging de la boundary puede admitir un futuro sufijo del mismo nombre, mientras
la policy del receptor sigue limitada al ARN exacto. Recrear el secreto prod
cambiaría su ARN y exigiría revisar permisos y referencias.

El módulo comparte ambientes y paquete Lambda: un futuro plan general puede
alterar staging y producción. Revisar siempre su alcance y el estado remoto antes
de autorizar cualquier despliegue. Las validaciones locales no detectan toda
divergencia entre Terraform y AWS. Esta actualización conserva Terraform y Lambda
sin cambios y no ejecuta `terraform apply`.

## Procedimiento de rollback

Este procedimiento es una guía para un cambio futuro autorizado; no se ejecuta
como parte de la actualización documental.

1. Identificar el fallo y capturar métricas y estados sin secretos ni tokens.
   Priorizar restaurar la última configuración/código compatible con Turnstile
   activo. Conservar el rechazo seguro ante fallos del proveedor.
2. Revisar el alcance productivo del rollback y coordinar cliente y receptor.
   No restaurar un receptor que solo admita un hostname si ambos siguen en uso.
   No modificar staging ni sustituir la boundary v3 por una versión anterior que
   retire permisos necesarios.
3. Si se autoriza explícitamente desactivar la protección para recuperar servicio,
   desactivar primero el flag del receptor prod y comprobar la actualización
   exitosa; después coordinar la desactivación del cliente en ambos hostnames.
   Registrar la ventana sin protección y aplicar vigilancia de abuso. No retirar
   primero el token del cliente mientras el receptor todavía lo exige.
4. Conservar secretos, metadatos y permisos para la recuperación; no borrar ni
   recrear secretos ni revocar permisos compartidos como mecanismo de rollback.
   Revisar cualquier plan antes de ejecutarlo, sin aplicar cambios generales.
5. Para reactivar, resolver la causa, verificar secreto/IAM y habilitar primero el
   cliente en ambos hostnames; comprobar tokens y luego activar el receptor prod.
   Probar formularios legítimos, notificaciones y rechazo sin token. Las pestañas
   antiguas pueden requerir recarga. Cerrar la ventana de rollback solo después
   de confirmar el funcionamiento en ambos dominios y la continuidad de staging.
