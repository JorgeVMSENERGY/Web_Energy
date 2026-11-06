# Turnstile: preparación de producción, fase 1

Rama `feature/forms-turnstile-production`, base `staging`. Cambios locales, sin
commit, push, merge, apply, despliegue ni modificaciones AWS.

## Configuración preparada

| Ambiente | Sitekey pública | Hostnames exactos | Activado |
| --- | --- | --- | --- |
| staging | `0x4AAAAAAF1Ixp8IY8H5Laik` | `jorgevmsenergy.github.io` | Sí |
| prod | `0x4AAAAAAF1Uw3YVAufKwYit` | `vmsenergy.com`, `www.vmsenergy.com` | No |

El widget productivo es Managed. El cliente conserva `interaction-only`; incorporar
la sitekey no carga el script ni ejecuta el widget mientras `enabled` sea false.
Los dos hostnames productivos conservan false explícito. El flujo staging no cambia.

El receptor recibe `TURNSTILE_HOSTNAMES` como lista JSON mediante Terraform. Valida
el hostname devuelto por Siteverify mediante pertenencia exacta, sin sufijos ni
comodines. Staging recibe exclusivamente su hostname GitHub Pages. La variable
singular `TURNSTILE_HOSTNAME` sigue siendo compatible con la Lambda staging ya
configurada, únicamente si la variable plural está ausente. Una lista plural vacía
o malformada no usa ese fallback y devuelve 503 cuando la verificación está activa.

Se conservan success estricto, acción igual al form_type validado, comprobación
antes de finalizar adjuntos/DynamoDB/SQS y rechazo controlado cuando Siteverify o
Secrets Manager no están disponibles. Tokens ausentes, inválidos, caducados o
reutilizados no se aceptan con el flag activo. Presign no consume tokens y no cambia.

## Aprovisionamiento y seguridad

`aws_secretsmanager_secret.turnstile` ahora itera ambos ambientes con independencia
de `turnstile_enabled`. La dirección existente `["staging"]` y su nombre se
conservan; no hay cambio de recurso ni eliminación por desactivar el flag.
La nueva dirección `["prod"]` crea únicamente metadatos para
`vmsenergy/forms/prod/turnstile`. No hay secret versions, lectura de valores como
data source ni variables de Terraform para la Secret Key.

Los valores se introducirán como SecretString crudo mediante Secrets Manager con
una identidad autorizada, fuera de Terraform, comandos con valores visibles,
archivos versionados, variables Lambda y logs. El código solo conoce el ARN y no
registra secretos, tokens ni excepciones sensibles de la verificación.

La policy de cada receptor prepara exclusivamente `secretsmanager:GetSecretValue`
sobre el ARN exacto de su propio recurso. Producción no puede leer el secreto
staging y viceversa. El notifier no recibe permisos nuevos. El flag false evita
consultar Secrets Manager y Siteverify, aunque ya exista el ARN o un valor.

Según el estado comunicado para esta fase, **VMSWebFormsExecutionBoundary v2 solo
autoriza el secreto staging**. No se consultó ni modificó AWS para este trabajo.
La policy del receptor no supera esa boundary: falta autorizar el secreto prod.

`infra/forms/turnstile-boundary-prod.statement.json` es la declaración adicional
mínima propuesta. Su Resource es un marcador deliberado: **no es una policy lista
para aplicar**. Después del aprovisionamiento deberá sustituirse por el ARN exacto
devuelto por Secrets Manager, incluido el sufijo asignado por AWS. No usar patrones
ni comodines. Añadirla a la versión vigente conservando todos los permisos staging;
no reemplazar el documento completo por esta declaración. El permiso se limita a
GetSecretValue; el cifrado administrado predeterminado no requiere una CMK propia.

El bloqueo de Terraform que rechaza `turnstile_enabled.prod=true` se conserva.
En consecuencia, crear el secreto, añadir IAM o almacenar su valor no activa
producción. Retirar ese bloqueo y cambiar los flags exige una futura modificación
revisada con autorización explícita de activación.

## Secuencia futura, pendiente de autorización

1. Revisar un plan de **solo aprovisionamiento de metadatos prod**. Debe mostrar
   la nueva dirección `["prod"]` sin destruir, reemplazar ni cambiar staging.
   No hacer un apply general: el módulo comparte infraestructura y paquete Lambda.
2. Crear los metadatos mediante el proceso autorizado y copiar únicamente el ARN
   al marcador de la declaración propuesta. Cargar el valor por el canal seguro
   indicado y revisar el widget Managed y sus dos hostnames en Cloudflare.
3. Autorizar por separado la ampliación de la boundary y la policy exacta del
   receptor prod. Comprobar permisos efectivos, incluidos SCP y resource policies.
   Conservar staging y los flags productivos en false durante esta preparación.
4. Revisar y autorizar el despliegue del receptor compatible con listas, sin
   activar prod, y del cliente con la sitekey preparada y enabled=false. Verificar
   regresión staging: formulario normal, careers con CV, presign y notificaciones.
   Los planes deben mostrar explícitamente qué Lambdas cambiarán; no incluir
   notifier ni componentes ajenos a formularios.
5. Solicitar autorización explícita de activación cuando secreto, permisos y
   pruebas estén listos. En un cambio posterior revisado, retirar el bloqueo
   Terraform y habilitar ambos hostnames del cliente y el flag servidor prod.
   Publicar primero el cliente habilitado, manteniendo temporalmente el servidor
   desactivado; comprobar obtención de tokens en ambos dominios y después activar
   el servidor. Esto evita rechazar inmediatamente páginas recién publicadas,
   pero las pestañas y clientes en caché anteriores pueden requerir recarga.
   La protección solo se considera activa tras habilitar el servidor.
6. Probar ambos dominios con envíos reales controlados, acción incorrecta, hostname
   ajeno, token ausente, inválido y reutilizado, incluyendo adjuntos. Confirmar que
   solo los envíos válidos crean leads/notificaciones y que no se guardan tokens.

Ante un fallo con el servidor activo, mantener el rechazo seguro. Cualquier rollback
de la protección requiere autorización y coordinación; no incorporar bypass por
fallos de proveedor. No tocar el secreto, widget ni flags staging como rollback prod.

## Pruebas y riesgos

La suite offline simula AWS y Siteverify. Amplía la regresión staging con ambos
hostnames prod, hostnames similares o cruzados entre ambientes, acción incorrecta,
tokens inválidos/reutilizados/ausentes, fallo del secreto y configuración inválida.
También verifica que un ARN productivo configurado no activa la verificación.

```powershell
python -B -m unittest discover -s infra/forms/tests -v
terraform -chdir=infra/forms fmt
terraform -chdir=infra/forms fmt -check
terraform -chdir=infra/forms validate
git diff --check
```

Estas comprobaciones no validan el secreto real, permisos efectivos, el widget en
navegador ni un plan AWS. Todo ello queda pendiente de las fases autorizadas.
Los riesgos principales son activar antes de disponer de secreto/permisos (503),
clientes antiguos sin token al activar (400), dependencia de Cloudflare y Secrets
Manager, y cambios involuntarios a staging mediante un apply general. Presign
conserva su superficie pública previa y los adjuntos subidos antes de un rechazo
siguen sujetos a la limpieza existente.
