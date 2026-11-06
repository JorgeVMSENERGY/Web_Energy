# Turnstile: estado y operación de staging

## Estado actual

Turnstile está activo y validado en staging y producción. La preparación y la
activación inicial ya terminaron. Consultar [el estado productivo y el procedimiento
de rollback](turnstile-production.md) para los detalles de producción.
Esta documentación usa el estado validado comunicado para el cierre del release;
la actualización documental no consulta ni modifica AWS.

| Ambiente | Sitekey pública | Hostnames exactos | Activo |
| --- | --- | --- | --- |
| staging | `0x4AAAAAAF1Ixp8IY8H5Laik` | `jorgevmsenergy.github.io` | Sí |
| prod | `0x4AAAAAAF1Uw3YVAufKwYit` | `vmsenergy.com`, `www.vmsenergy.com` | Sí |

Los formularios productivos se probaron en ambos hostnames y las notificaciones
se recibieron correctamente. La API productiva rechaza solicitudes sin token con
HTTP 400 `turnstile_required`. La Lambda `vmsenergy-forms-prod-receiver` tiene
`TURNSTILE_ENABLED=true`, estado `Active` y `LastUpdateStatus=Successful`.

## Arquitectura

El cliente obtiene un token nuevo por intento y lo envía como `turnstile_token`
en `/submissions`, después de subir adjuntos. El widget Managed usa apariencia
`interaction-only`; la acción corresponde al `form_type` validado por el servidor.
No se incorporan tokens a `fields` ni a mensajes de analítica.

El receptor consulta Siteverify por HTTPS con timeout de 5 segundos. Exige éxito
estricto, hostname exacto permitido y acción igual al tipo de formulario. Staging
solo admite `jorgevmsenergy.github.io`; producción admite sus dos hostnames.
No se aceptan coincidencias por sufijo ni Origin o `page_url` como autenticidad.
La variable plural `TURNSTILE_HOSTNAMES` usa una lista JSON; la singular heredada
solo se usa si la plural está ausente, sin ampliar los hostnames permitidos.

La verificación ocurre antes de finalizar adjuntos, crear el lead o enviar a SQS.
Un token ausente devuelve 400 `turnstile_required` si las validaciones previas de
la solicitud pasan; uno rechazado devuelve 400 `turnstile_invalid`. Fallos de red,
secreto, configuración o proveedor devuelven 503 `turnstile_unavailable`, sin
bypass ni detalles sensibles. Los tokens caducados o reutilizados se rechazan.
`/uploads/presign` conserva el comportamiento previo y no consume tokens.

## Secrets Manager e IAM

Secrets Manager e IAM están configurados para ambos ambientes. Terraform administra
únicamente metadatos, independientemente de la activación. Los valores son
SecretString crudos administrados fuera de Terraform; no hay versiones ni lecturas
de valores como data sources. No copiar Secret Keys o tokens a Git, comandos,
variables Lambda, logs o documentación.

| Ambiente | ARN del secreto |
| --- | --- |
| staging | `arn:aws:secretsmanager:us-east-1:787140332697:secret:vmsenergy/forms/staging/turnstile-kRofj2` |
| prod | `arn:aws:secretsmanager:us-east-1:787140332697:secret:vmsenergy/forms/prod/turnstile-q8Vt0E` |

El receptor lee el secreto en cada verificación, con timeouts AWS de 2 segundos
y un solo intento. Rotar el valor no requiere publicar código, pero debe
coordinarse con Cloudflare. Se utiliza el cifrado administrado de Secrets Manager.
Cada rol receptor concede únicamente `secretsmanager:GetSecretValue` sobre su
propio secreto exacto; el notifier no recibe ese permiso.

La permissions boundary vigente es **VMSWebFormsExecutionBoundary v3**. Conserva
las siete declaraciones previas y agrega `ReadProductionTurnstileSecret`, cuya
única acción es GetSecretValue sobre el ARN productivo exacto terminado en
`q8Vt0E`. `ReadStagingTurnstileSecret` conserva el Resource
`arn:aws:secretsmanager:us-east-1:787140332697:secret:vmsenergy/forms/staging/turnstile-??????`.
Los seis `?` corresponden al sufijo asignado por AWS; la policy del rol staging
sigue limitada al ARN exacto terminado en `kRofj2`.

Los archivos `infra/forms/turnstile-boundary-staging.statement.json` y
`infra/forms/turnstile-boundary-prod.statement.json` contienen **declaraciones de
referencia, NO documentos de policy completos para aplicar**. No reemplazar la
boundary con estos fragmentos; conservar sus demás declaraciones. La boundary
limita permisos y no los concede; este módulo no administra su documento.
La simulación del rol productivo mostró `allowed` para su secreto e `implicitDeny`
para staging, confirmando el aislamiento de ese rol. No es una denegación al rol
staging. Las antiguas versiones v1/v2 no representan la boundary vigente.

## Validación y operación

Desde la raíz:

```powershell
python -B -m unittest discover -s infra/forms/tests -v
terraform -chdir=infra/forms fmt -check
terraform -chdir=infra/forms validate
git diff --check
```

La suite offline simula AWS y Siteverify y cubre éxito, tokens ausentes, inválidos,
caducados o duplicados, hostname/acción incorrectos, fallos del secreto/proveedor,
configuración incorrecta y flujo de adjuntos. Sus casos con el flag desactivado
son escenarios de regresión del código, no el estado actual de producción.
No sustituye la comprobación real del widget, IAM y las notificaciones. Staging
ya fue validado; no repetir el aprovisionamiento ni sustituir su secreto.

Revisar errores 400/503, estado de Lambda y recepción de notificaciones sin
registrar tokens ni secretos. Fallos de Cloudflare/Secrets Manager impiden envíos
y agregan latencia. Un script bloqueado o cliente antiguo puede impedir obtener
el token; verificar con una recarga y un envío controlado.

Presign conserva su superficie pública previa. Los archivos subidos antes de un
rechazo siguen sujetos a la limpieza existente. El módulo comparte ambientes y
paquete Lambda: revisar el alcance de cualquier plan futuro para evitar cambios
involuntarios en producción. No ejecutar un apply general como reparación rutinaria.

Para un rollback staging autorizado, priorizar restaurar una versión compatible
con la protección activa. Si se autoriza desactivarla, coordinar primero el flag
servidor y después el cliente, registrando la ventana sin protección. Conservar
secreto y permisos, sin tocar producción. Para reactivar, verificar secreto/IAM,
habilitar cliente antes del receptor y probar envíos y notificaciones. Mantener
rechazo seguro ante fallos mientras el flag servidor esté activo.
