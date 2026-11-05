# Turnstile: integración y activación gradual

> Este documento conserva el plan inicial de staging. Para el estado de preparación
> productiva, la boundary v2 y las siguientes fases, consultar
> [turnstile-production.md](turnstile-production.md). Staging ya fue validado;
> no repetir su aprovisionamiento ni sustituir su secreto.

Preparado en `feature/forms-turnstile`, sin despliegues. No se encontró una suite
de pruebas existente para formularios; se incorpora una suite offline del receptor.

## Arquitectura

`vms-formularios.js` cubre todos los `form[data-vms-form-type]`, incluidos los de
la carpeta `en`. Inserta el widget de forma explícita al enviar, con apariencia
`interaction-only`. En Cloudflare el widget debe estar configurado como **Managed**;
ese modo se configura en el dashboard, no en JavaScript. Staging usa sitekey
`0x4AAAAAAF1Ixp8IY8H5Laik` y hostname exacto `jorgevmsenergy.github.io`.

Cada intento obtiene un token nuevo después de subir los adjuntos y lo envía como
`turnstile_token` en `/submissions`. La acción es el `form_type` validado por el
servidor: quick_lead, contact, cta, careers, suggestions, supplier, more_info o
stand_rim. El widget se elimina al terminar; no hay reintentos automáticos del POST.
Los tokens no se incorporan a `fields` ni a mensajes de analítica.

El receptor valida Siteverify por HTTPS con timeout de 5 segundos. Exige
`success is True`, hostname exacto y acción igual al tipo de formulario. No acepta
hostnames por sufijo, Origin ni `page_url` como prueba de autenticidad. Cloudflare
rechaza tokens caducados y reutilizados; no se almacenan resultados ni tokens.
La validación ocurre antes de finalizar adjuntos, crear el lead o enviar a SQS.
Un token ausente o rechazado devuelve 400; fallo de red, secreto o proveedor,
configuración incompleta o respuesta malformada devuelve 503 sin detalles sensibles.

`/uploads/presign`, límites de archivos y CORS permanecen como estaban. No consume
el token que después necesita `/submissions`. `api.tf` fue revisado y no requiere
cambios porque el token viaja dentro del JSON actual.

## Secreto e IAM

Terraform crea solamente metadatos de Secrets Manager para staging y producción:
`vmsenergy/forms/staging/turnstile` y `vmsenergy/forms/prod/turnstile`.
El aprovisionamiento es independiente de la activación. **No** crea versiones, consulta valores ni
recibe la Secret Key como variable. El estado y la configuración Lambda contienen
únicamente el ARN, hostname y flag. Se usa el cifrado administrado de Secrets
Manager; no hace falta conceder `kms:Decrypt` de una CMK. El receptor obtiene el
`SecretString` crudo en cada verificación, con timeouts AWS de 2 segundos y un solo
intento. Rotar el valor no requiere publicar el código. No usar JSON como valor.

Introducir el valor mediante la consola de Secrets Manager, con una identidad
administrativa autorizada y sin copiarlo a comandos, archivos, Terraform, Git,
variables Lambda o logs. No se necesita proporcionar la Secret Key en este chat.
El receptor solo recibe `secretsmanager:GetSecretValue` sobre el ARN exacto de su
ambiente; el notifier no recibe este permiso. La policy del receptor productivo
queda preparada, pero la boundary v2 todavía limita Secrets Manager a staging.

### Historial de la boundary

Se consultó `VMSWebFormsExecutionBoundary`, versión predeterminada `v1`, el
2026-11-05. Su documento no permite ninguna operación de Secrets Manager:
En esa revisión, **la boundary v1 era incompatible** con la lectura del secreto.
Según el estado comunicado para la fase productiva, v2 ya autoriza staging y
todavía no autoriza producción. No se consultó ni modificó AWS en esta nueva fase.

`infra/forms/turnstile-boundary-staging.statement.json` contiene la declaración
mínima propuesta para añadir a la policy existente, conservando todas las demás
declaraciones. Los seis `?` cubren solo el sufijo que AWS asigna al ARN del secreto;
al conocerse el ARN puede usarse su valor exacto. La boundary limita permisos, no
los concede: la policy del receptor sigue limitada al ARN exacto. El administrador
deberá revisar también SCP, resource policies y cualquier deny explícito; comprobar
el permiso efectivo con IAM Policy Simulator para el rol staging y el ARN real.
No desplegar el flag activo hasta resolver esta dependencia. No hay cambios a
la boundary administrados por Terraform en este módulo.

## Plan gradual (pendiente de ejecución autorizada)

1. Confirmar Managed y restringir el widget al hostname staging en Cloudflare.
2. Revisar y autorizar la ampliación mínima de la boundary anterior. Crear solo
   los metadatos del secreto staging y cargar el valor por el canal seguro descrito.
3. Preparar un plan limitado a recursos de staging: secreto, policy del receptor
   y Lambda receptora. Revisar que no incluya recursos `prod`, notifier ni sitio
   productivo. **No ejecutar un apply general**: este módulo comparte los dos
   ambientes y el paquete receptor; un plan general también actualizaría producción.
4. Publicar el receptor staging con `turnstile_enabled.staging=true`, verificar
   los permisos efectivos y después publicar el cliente en GitHub Pages staging.
   Coordinar ambos pasos: un cliente viejo sin token será rechazado cuando el
   receptor esté activo; programar una ventana de cambio para evitar rechazos.
5. Probar cada tipo de formulario en navegador, incluidos formularios ocultos,
   múltiples formularios por página y careers con CV. Probar también bloqueo del
   script, token ausente, acción/hostname incorrectos y segundo uso del mismo token.
   Confirmar el lead y notificación solo para validaciones correctas; revisar que
   DynamoDB, SQS y logs no contengan tokens ni secretos.
6. Mantener producción desactivada. Su flag está en false en cliente y servidor,
   y una validación Terraform impide activarlo accidentalmente. La preparación
   posterior incorpora la sitekey y ambos hostnames productivos, así como metadatos
   del futuro secreto, sin activación. Su Secret Key aún debe almacenarse en AWS;
   la habilitación requiere autorización nueva y el plan productivo enlazado arriba.

Si se revierte staging, mantener el rechazo seguro o restaurar la versión previa
mediante un cambio autorizado y coordinado de ambos flags. Nunca introducir un
fallback que admita solicitudes mientras el flag servidor siga activo.

## Validaciones y límites

Ejecutar desde la raíz:

```powershell
python -B -m unittest discover -s infra/forms/tests -v
terraform -chdir=infra/forms fmt -check
terraform -chdir=infra/forms validate
git diff --check
```

Las pruebas simulan Siteverify y AWS sin acceso a red. Cubren éxito, token ausente,
inválido, caducado, duplicado, timeout, hostname y acción incorrectos, las ocho
acciones, errores de Secrets Manager/proveedor, ausencia de efectos persistentes,
flujo de adjuntos y producción desactivada. No sustituyen la prueba real del widget
y de los permisos efectivos una vez configurado el secreto. No hay Node.js local
disponible para ejecutar pruebas del cliente; queda pendiente verificación en navegador.

Riesgos: dependencia de Cloudflare/Secrets Manager y latencia adicional; una caída
rechaza envíos con 503. El script bloqueado muestra error y no envía el formulario.
Si falla la validación después de subir archivos, permanecen pendientes bajo la
política de expiración ya existente. Presign conserva su superficie pública previa
y no queda protegido por Turnstile con este cambio. La infraestructura compartida
exige revisar el alcance del futuro plan para evitar actualizar producción.

Referencia: [validación server-side de Cloudflare](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
y [renderizado del widget](https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/).
